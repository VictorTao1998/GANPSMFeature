"""
Author: Isabella Liu 9/7/21
Feature: Train cycle GAN with PSMNet
"""
import gc
import os
import argparse
import numpy as np
import tensorboardX
import torch
import torch.backends.cudnn as cudnn
import torch.distributed as dist
import torch.nn.functional as F

from datasets.messytable import MessytableDataset
from nets.psmnet import PSMNet
from nets.cycle_gan import CycleGANModel
from utils.cascade_metrics import compute_err_metric
from utils.warp_ops import apply_disparity_cu
from utils.config import cfg
from utils.reduce import set_random_seed, synchronize, AverageMeterDict, \
    tensor2float, tensor2numpy, reduce_scalar_outputs, make_nograd_func
from utils.util import setup_logger, weights_init, \
    adjust_learning_rate, save_scalars, save_scalars_graph, save_images, save_images_grid, disp_error_img

cudnn.benchmark = True

parser = argparse.ArgumentParser(description='CycleGAN with Pyramid Stereo Network (PSMNet)')
parser.add_argument('--config-file', type=str, default='./configs/local_train_steps.yaml',
                    metavar='FILE', help='Config files')
parser.add_argument('--summary-freq', type=int, default=500, help='Frequency of saving temporary results')
parser.add_argument('--save-freq', type=int, default=1000, help='Frequency of saving checkpoint')
parser.add_argument('--logdir', required=True, help='Directory to save logs and checkpoints')
parser.add_argument('--seed', type=int, default=1, metavar='S', help='Random seed (default: 1)')
parser.add_argument("--local_rank", type=int, default=0, help='Rank of device in distributed training')
parser.add_argument('--debug', action='store_true', help='Whether run in debug mode (will load less data)')
parser.add_argument('--warp-op', action='store_true',default=True, help='whether use warp_op function to get disparity')
parser.add_argument('--loss-ratio', type=float, default=0.05, help='Ratio between loss_G and loss_cascade')
parser.add_argument('--loadmodel', default= None, help='load model')

args = parser.parse_args()
cfg.merge_from_file(args.config_file)

# Set random seed to make sure networks in different processes are same
set_random_seed(args.seed)

# Set up distributed training
num_gpus = int(os.environ["WORLD_SIZE"]) if "WORLD_SIZE" in os.environ else 1
is_distributed = num_gpus > 1
args.is_distributed = is_distributed
#print(is_distributed)
if is_distributed:
    torch.cuda.set_device(args.local_rank)
    torch.distributed.init_process_group( backend="nccl", init_method="env://")
    synchronize()
cuda_device = torch.device("cuda:{}".format(args.local_rank))

# Set up tensorboard and logger
os.makedirs(args.logdir, exist_ok=True)
os.makedirs(os.path.join(args.logdir, 'models'), exist_ok=True)
summary_writer = tensorboardX.SummaryWriter(logdir=args.logdir)
logger = setup_logger("CycleGAN PSMNet", distributed_rank=args.local_rank, save_dir=args.logdir)
logger.info(f'Loaded config file: \'{args.config_file}\'')
logger.info(f'Running with configs:\n{cfg}')
logger.info(f'Running with {num_gpus} GPUs')

# python -m torch.distributed.launch train_cycleGAN_psmnet.py --summary-freq 1 --save-freq 1 --logdir ../train_9_7_cyclegan_psmnet/debug --debug
# python -m torch.distributed.launch train_cycleGAN_psmnet.py --config-file configs/remote_train_steps.yaml --summary-freq 10 --save-freq 100 --logdir ../train_9_7_cyclegan_psmnet/debug --debug


def train(gan_model, psmnet_model, feaex, TrainImgLoader, ValImgLoader):
    cur_err = np.inf    # store best result

    for epoch_idx in range(cfg.SOLVER.EPOCHS):
        print('epoch: ', epoch_idx)
        # One epoch training loop
        avg_train_scalars_gan = AverageMeterDict()
        avg_train_scalars_psmnet = AverageMeterDict()
        for batch_idx, sample in enumerate(TrainImgLoader):
            print('iter: ', batch_idx)
            global_step = (len(TrainImgLoader) * epoch_idx + batch_idx) * cfg.SOLVER.BATCH_SIZE


            # Adjust learning rate
            adjust_learning_rate(gan_model.optimizer_G, global_step, cfg.SOLVER.LR_G, cfg.SOLVER.LR_STEPS)
            adjust_learning_rate(gan_model.optimizer_D, global_step, cfg.SOLVER.LR_D, cfg.SOLVER.LR_STEPS)
            #adjust_learning_rate(psmnet_optimizer, global_step, cfg.SOLVER.LR_CASCADE, cfg.SOLVER.LR_STEPS)

            do_summary = global_step % args.summary_freq == 0
            # Train one sample
            scalar_outputs_gan, img_outputs_gan, img_outputs_psmnet, scalar_outputs_psmnet = \
                train_sample(sample, gan_model, psmnet_model, feaex, isTrain=True)
            # Save result to tensorboard
            if (not is_distributed) or (dist.get_rank() == 0):
                scalar_outputs_gan = tensor2float(scalar_outputs_gan)
                scalar_outputs_psmnet = tensor2float(scalar_outputs_psmnet)
                avg_train_scalars_gan.update(scalar_outputs_gan)
                avg_train_scalars_psmnet.update(scalar_outputs_psmnet)
                if do_summary:
                    # Update GAN images
                    save_images_grid(summary_writer, 'train_gan', img_outputs_gan, global_step)
                    # Update GAN losses
                    scalar_outputs_gan.update({'lr_G': gan_model.optimizer_G.param_groups[0]['lr']})
                    scalar_outputs_gan.update({'lr_D': gan_model.optimizer_D.param_groups[0]['lr']})
                    save_scalars_graph(summary_writer, 'train_gan', scalar_outputs_gan, global_step)
                    # Update PSMNet images
                    save_images(summary_writer, 'train_psmnet', img_outputs_psmnet, global_step)
                    # Update PSMNet losses
                    scalar_outputs_psmnet.update({'lr': psmnet_optimizer.param_groups[0]['lr']})
                    save_scalars(summary_writer, 'train_psmnet', scalar_outputs_psmnet, global_step)

                # Save checkpoints
                if (global_step + 1) % args.save_freq == 0:
                    checkpoint_data = {
                        'epoch': epoch_idx,
                        'G_A': gan_model.netG_A.state_dict(),
                        'G_B': gan_model.netG_B.state_dict(),
                        'D_A': gan_model.netD_A.state_dict(),
                        'D_B': gan_model.netD_B.state_dict(),
                        'PSMNet': psmnet_model.state_dict(),
                        'optimizerG': gan_model.optimizer_G.state_dict(),
                        'optimizerD': gan_model.optimizer_D.state_dict()
                    }
                    save_filename = os.path.join(args.logdir, 'models', f'model_{global_step}.pth')
                    torch.save(checkpoint_data, save_filename)

                    # Get average results among all batches
                    total_err_metric_gan = avg_train_scalars_gan.mean()
                    total_err_metric_psmnet = avg_train_scalars_psmnet.mean()
                    logger.info(f'Step {global_step} train gan    : {total_err_metric_gan}')
                    logger.info(f'Step {global_step} train cascade: {total_err_metric_psmnet}')
            del scalar_outputs_gan, img_outputs_gan, img_outputs_psmnet, scalar_outputs_psmnet
        gc.collect()
        """
        # One epoch validation loop
        avg_val_scalars_gan = AverageMeterDict()
        avg_val_scalars_psmnet = AverageMeterDict()
        for batch_idx, sample in enumerate(ValImgLoader):
            global_step = (len(ValImgLoader) * epoch_idx + batch_idx) * cfg.SOLVER.BATCH_SIZE
            do_summary = global_step % args.summary_freq == 0
            scalar_outputs_gan, scalar_outputs_psmnet, img_outputs_gan, img_outputs_psmnet = \
                train_sample(sample, gan_model, psmnet_model, psmnet_optimizer, isTrain=False)
            if (not is_distributed) or (dist.get_rank() == 0):
                scalar_outputs_gan = tensor2float(scalar_outputs_gan)
                scalar_outputs_psmnet = tensor2float(scalar_outputs_psmnet)
                avg_val_scalars_gan.update(scalar_outputs_gan)
                avg_val_scalars_psmnet.update(scalar_outputs_psmnet)
                if do_summary:
                    save_images_grid(summary_writer, 'val_gan', img_outputs_gan, global_step)
                    scalar_outputs_gan.update({'lr_G': gan_model.optimizer_G.param_groups[0]['lr']})
                    scalar_outputs_gan.update({'lr_D': gan_model.optimizer_D.param_groups[0]['lr']})
                    save_scalars_graph(summary_writer, 'val_gan', scalar_outputs_gan, global_step)
                    save_images(summary_writer, 'val_psmnet', img_outputs_psmnet, global_step)
                    scalar_outputs_psmnet.update({'lr': psmnet_optimizer.param_groups[0]['lr']})
                    save_scalars(summary_writer, 'val_psmnet', scalar_outputs_psmnet, global_step)
        

        if (not is_distributed) or (dist.get_rank() == 0):
            # Get average results among all batches
            total_err_metric_gan = avg_val_scalars_gan.mean()
            total_err_metric_psmnet = avg_val_scalars_psmnet.mean()
            logger.info(f'Epoch {epoch_idx} val   gan    : {total_err_metric_gan}')
            logger.info(f'Epoch {epoch_idx} val   psmnet : {total_err_metric_psmnet}')

            # Save best checkpoints
            new_err = total_err_metric_psmnet['depth_abs_err'][0] if num_gpus > 1 \
                else total_err_metric_psmnet['depth_abs_err']
            if new_err < cur_err:
                cur_err = new_err
                checkpoint_data = {
                    'epoch': epoch_idx,
                    'G_A': gan_model.netG_A.state_dict(),
                    'G_B': gan_model.netG_B.state_dict(),
                    'D_A': gan_model.netD_A.state_dict(),
                    'D_B': gan_model.netD_B.state_dict(),
                    'PSMNet': psmnet_model.state_dict(),
                    'optimizerG': gan_model.optimizer_G.state_dict(),
                    'optimizerD': gan_model.optimizer_D.state_dict(),
                    'optimizerPSMNet': psmnet_optimizer.state_dict()
                }
                save_filename = os.path.join(args.logdir, 'models', f'model_best.pth')
                torch.save(checkpoint_data, save_filename)
        """
        gc.collect()


def train_sample(sample, gan_model, psmnet_model, feaex, isTrain=True):
    if isTrain:
        gan_model.train()
        psmnet_model.train()
        feaex.eval()
    else:
        gan_model.eval()
        psmnet_model.eval()
        feaex.eval()

    psmnet_model.module.feature_extraction.gan_train = True

    # Train on GAN
    img_L = sample['img_L'].to(cuda_device)  # [bs, 1, H, W]
    img_R = sample['img_R'].to(cuda_device)  # [bs, 1, H, W]
    img_sim = sample['img_sim'].to(cuda_device)  # [bs, 1, 2H, 2W]

    #img_sim = F.interpolate(img_sim, scale_factor=0.5, mode='bilinear',
    #                         recompute_scale_factor=False, align_corners=False)

    #print(img_L.shape, img_R.shape, img_sim.shape)
    
    img_L = F.interpolate(img_L, scale_factor=0.5, mode='bilinear',
                             recompute_scale_factor=False, align_corners=False)
    img_R = F.interpolate(img_R, scale_factor=0.5, mode='bilinear',
                             recompute_scale_factor=False, align_corners=False)

    #print(img_L.shape, img_R.shape, img_sim.shape)
    fea_L_f, fea_R_f, fea_sim_f = feaex(img_L).detach(), feaex(img_R).detach(), feaex(img_sim).detach()
    #print(fea_L_f[0,0,0,0] == fea_L_f[0,1,0,0])
    #fea_L = fea_L_f[:,0,:,:].reshape((fea_L_f.shape[0], 1, fea_L_f.shape[2], fea_L_f.shape[3]))
    #fea_R = fea_R_f[:,0,:,:].reshape((fea_R_f.shape[0], 1, fea_R_f.shape[2], fea_R_f.shape[3]))
    #fea_sim = fea_sim_f[:,0,:,:].reshape((fea_sim_f.shape[0], 1, fea_sim_f.shape[2], fea_sim_f.shape[3]))

    input_sample = {'img_L': fea_L_f, 'img_R': fea_R_f, 'img_sim': fea_sim_f}
    gan_model.set_input(input_sample)
    if isTrain:
        gan_model.forward()
    else:
        with torch.no_grad():
            gan_model.forward()

    # Train on Cascade
    fake_img_L = gan_model.fake_B_L.to(cuda_device)    # [bs, 1, H, W]
    fake_img_R = gan_model.fake_B_R.to(cuda_device)    # [bs, 1, H, W]
    range_L_o = torch.max(fea_L_f) -torch.min(fea_L_f)
    range_R_o = torch.max(fea_R_f) -torch.min(fea_R_f)
    range_sim_o = torch.max(fea_sim_f) -torch.min(fea_sim_f)
    range_L_g = torch.max(fake_img_L) -torch.min(fake_img_L)
    range_R_g = torch.max(fake_img_R) -torch.min(fake_img_R)
    #print('range L before: ', range_L_o, ' range R before: ', range_R_o,  ' range sim before: ', range_sim_o,  ' range L after: ', range_L_g, ' range R after: ', range_R_g)
    disp_gt = sample['img_disp_l'].to(cuda_device)
    depth_gt = sample['img_depth_l'].to(cuda_device)  # [bs, 1, H, W]
    img_focal_length = sample['focal_length'].to(cuda_device)
    img_baseline = sample['baseline'].to(cuda_device)

    # Resize the 2x resolution disp and depth back to H * W
    # Note this should go before apply_disparity_cu
    disp_gt = F.interpolate(disp_gt, scale_factor=0.5, mode='nearest',
                             recompute_scale_factor=False)  # [bs, 1, H, W]
    depth_gt = F.interpolate(depth_gt, scale_factor=0.5, mode='nearest',
                             recompute_scale_factor=False)  # [bs, 1, H, W]

    if args.warp_op:
        img_disp_r = sample['img_disp_r'].to(cuda_device)
        img_disp_r = F.interpolate(img_disp_r, scale_factor=0.5, mode='nearest',
                                   recompute_scale_factor=False)
        disp_gt = apply_disparity_cu(img_disp_r, img_disp_r.type(torch.int))  # [bs, 1, H, W]
        del img_disp_r

    mask = (disp_gt < cfg.ARGS.MAX_DISP) * (disp_gt > 0)  # Note in training we do not exclude bg
    if isTrain:
        pred_disp1, pred_disp2, pred_disp3 = psmnet_model(fake_img_L, fake_img_R)
        pred_disp = pred_disp3
        loss_psmnet = 0.5 * F.smooth_l1_loss(pred_disp1[mask], disp_gt[mask], reduction='mean') \
               + 0.7 * F.smooth_l1_loss(pred_disp2[mask], disp_gt[mask], reduction='mean') \
               + F.smooth_l1_loss(pred_disp3[mask], disp_gt[mask], reduction='mean')
    else:
        with torch.no_grad():
            pred_disp = psmnet_model(fea_L_f, fea_R_f)
            loss_psmnet = F.smooth_l1_loss(pred_disp[mask], disp_gt[mask], reduction='mean')

    # Backward and optimization
    if isTrain:
        # update Ds
        gan_model.update_D()
        # Update Gs
        total_loss = gan_model.compute_loss_G() + loss_psmnet * args.loss_ratio # loss_G + loss_psmnet (task loss)
        # Ds require no gradient when optimizing Gs
        gan_model.set_requires_grad([gan_model.netD_A, gan_model.netD_B], False)
        gan_model.optimizer_G.zero_grad()   # set Gs' gradient to zero
        psmnet_optimizer.zero_grad()           # set cascade gradient to zero
        total_loss.backward()                   # calculate gradient
        gan_model.optimizer_G.step()            # update Gs weights
        psmnet_optimizer.step()                # update cascade weights
    else:
        gan_model.compute_loss_G()
        gan_model.compute_loss_D_A()
        gan_model.compute_loss_D_B()

    # Save gan scalar outputs and images
    scalar_outputs_gan = {
        'G_A': gan_model.loss_G_A, 'G_B': gan_model.loss_G_B,
        'cycle_A': gan_model.loss_cycle_A, 'cycle_B': gan_model.loss_cycle_B,
        'idt_A': gan_model.loss_idt_A, 'idt_B': gan_model.loss_idt_B,
        'D_A': gan_model.loss_D_A, 'D_B': gan_model.loss_D_B
    }
    img_outputs_gan = {
        'img_L_0': {
            'input': gan_model.real_A_L[:,0,:,:][:,None,:,:], 'fake': gan_model.fake_B_L[:,0,:,:][:,None,:,:], 'rec': gan_model.rec_A_L[:,0,:,:][:,None,:,:], 'idt': gan_model.idt_B_L[:,0,:,:][:,None,:,:]
        },
        'img_L_1': {
            'input': gan_model.real_A_L[:,1,:,:][:,None,:,:], 'fake': gan_model.fake_B_L[:,1,:,:][:,None,:,:], 'rec': gan_model.rec_A_L[:,1,:,:][:,None,:,:], 'idt': gan_model.idt_B_L[:,1,:,:][:,None,:,:]
        },
        'img_L_2': {
            'input': gan_model.real_A_L[:,2,:,:][:,None,:,:], 'fake': gan_model.fake_B_L[:,2,:,:][:,None,:,:], 'rec': gan_model.rec_A_L[:,2,:,:][:,None,:,:], 'idt': gan_model.idt_B_L[:,2,:,:][:,None,:,:]
        },
        'img_R_0': {
            'input': gan_model.real_A_R[:,0,:,:][:,None,:,:], 'fake': gan_model.fake_B_R[:,0,:,:][:,None,:,:], 'rec': gan_model.rec_A_R[:,0,:,:][:,None,:,:], 'idt': gan_model.idt_B_R[:,0,:,:][:,None,:,:]
        },
        'img_R_1': {
            'input': gan_model.real_A_R[:,1,:,:][:,None,:,:], 'fake': gan_model.fake_B_R[:,1,:,:][:,None,:,:], 'rec': gan_model.rec_A_R[:,1,:,:][:,None,:,:], 'idt': gan_model.idt_B_R[:,1,:,:][:,None,:,:]
        },
        'img_R_2': {
            'input': gan_model.real_A_R[:,2,:,:][:,None,:,:], 'fake': gan_model.fake_B_R[:,2,:,:][:,None,:,:], 'rec': gan_model.rec_A_R[:,2,:,:][:,None,:,:], 'idt': gan_model.idt_B_R[:,2,:,:][:,None,:,:]
        },
        'img_Sim_0': {
            'input': gan_model.real_B[:,0,:,:][:,None,:,:], 'fake': gan_model.fake_A[:,0,:,:][:,None,:,:], 'rec': gan_model.rec_B[:,0,:,:][:,None,:,:], 'idt': gan_model.idt_A[:,0,:,:][:,None,:,:]
        },
        'img_Sim_1': {
            'input': gan_model.real_B[:,1,:,:][:,None,:,:], 'fake': gan_model.fake_A[:,1,:,:][:,None,:,:], 'rec': gan_model.rec_B[:,1,:,:][:,None,:,:], 'idt': gan_model.idt_A[:,1,:,:][:,None,:,:]
        },
        'img_Sim_2': {
            'input': gan_model.real_B[:,2,:,:][:,None,:,:], 'fake': gan_model.fake_A[:,2,:,:][:,None,:,:], 'rec': gan_model.rec_B[:,2,:,:][:,None,:,:], 'idt': gan_model.idt_A[:,2,:,:][:,None,:,:]
        }
    }

    # Compute cascade error metrics
    scalar_outputs_psmnet = {'loss': loss_psmnet.item()}
    err_metrics = compute_err_metric(disp_gt,
                                     depth_gt,
                                     pred_disp,
                                     img_focal_length,
                                     img_baseline,
                                     mask)
    scalar_outputs_psmnet.update(err_metrics)
    # Compute error images
    pred_disp_err_np = disp_error_img(pred_disp[[0]], disp_gt[[0]], mask[[0]])
    pred_disp_err_tensor = torch.from_numpy(np.ascontiguousarray(pred_disp_err_np[None].transpose([0, 3, 1, 2])))
    img_outputs_psmnet = {
        'disp_gt': disp_gt[[0]].repeat([1, 3, 1, 1]),
        'disp_pred': pred_disp[[0]].repeat([1, 3, 1, 1]),
        'disp_err': pred_disp_err_tensor
    }

    if is_distributed:
        scalar_outputs_gan = reduce_scalar_outputs(scalar_outputs_gan, cuda_device)
        scalar_outputs_psmnet = reduce_scalar_outputs(scalar_outputs_psmnet, cuda_device)
    return scalar_outputs_gan, img_outputs_gan, img_outputs_psmnet, scalar_outputs_psmnet


if __name__ == '__main__':
    # Obtain dataloader
    train_dataset = MessytableDataset(cfg.REAL.TRAIN, debug=args.debug, sub=600, isReal=True)
    val_dataset = MessytableDataset(cfg.REAL.TRAIN, debug=args.debug, sub=100, isReal=True)
    if is_distributed:
        train_sampler = torch.utils.data.DistributedSampler(train_dataset, num_replicas=dist.get_world_size(),
                                                            rank=dist.get_rank())
        val_sampler = torch.utils.data.DistributedSampler(val_dataset, num_replicas=dist.get_world_size(),
                                                          rank=dist.get_rank())

        TrainImgLoader = torch.utils.data.DataLoader(train_dataset, cfg.SOLVER.BATCH_SIZE, sampler=train_sampler,
                                                     num_workers=cfg.SOLVER.NUM_WORKER, drop_last=True, pin_memory=True)
        ValImgLoader = torch.utils.data.DataLoader(val_dataset, cfg.SOLVER.BATCH_SIZE, sampler=val_sampler,
                                                   num_workers=cfg.SOLVER.NUM_WORKER, drop_last=False, pin_memory=True)
    else:
        TrainImgLoader = torch.utils.data.DataLoader(train_dataset, batch_size=cfg.SOLVER.BATCH_SIZE,
                                                     shuffle=True, num_workers=cfg.SOLVER.NUM_WORKER, drop_last=True)

        ValImgLoader = torch.utils.data.DataLoader(val_dataset, batch_size=cfg.SOLVER.BATCH_SIZE,
                                                   shuffle=False, num_workers=cfg.SOLVER.NUM_WORKER, drop_last=False)

    # Create GAN model
    gan_model = CycleGANModel()
    gan_model.set_device(cuda_device)
    gan_model.set_distributed(is_distributed=is_distributed, local_rank=args.local_rank)

    # Create PSMNet model
    psmnet_model = PSMNet(maxdisp=cfg.ARGS.MAX_DISP).to(cuda_device)
    psmnet_optimizer = torch.optim.Adam(psmnet_model.parameters(), lr=cfg.SOLVER.LR_CASCADE, betas=(0.9, 0.999))
    if is_distributed:
        psmnet_model = torch.nn.parallel.DistributedDataParallel(
            psmnet_model, device_ids=[args.local_rank], output_device=args.local_rank)
    else:
        psmnet_model = torch.nn.DataParallel(psmnet_model)

    pretrain_dict = torch.load(args.loadmodel)
    psmnet_model.load_state_dict(pretrain_dict['PSMNet'])

    feaex = psmnet_model.module.feature_extraction.ganfeature
    psmnet_model.module.feature_extraction.gan_train = False

    # Start training
    train(gan_model, psmnet_model, feaex, TrainImgLoader, ValImgLoader)