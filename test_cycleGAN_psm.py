"""
Author: Isabella Liu 9/7/21
Feature: Test cycleGAN + PSMNet
"""

import os
import argparse
import torch
import torch.nn.functional as F
from tqdm import tqdm
import numpy as np
import tensorboardX

from nets.cycle_gan import CycleGANModel
from nets.psmnet import PSMNet
from datasets.messytable_test import get_test_loader
from utils.cascade_metrics import compute_err_metric, compute_obj_err
from utils.config import cfg
from utils.util import get_time_string, setup_logger, depth_error_img, disp_error_img, save_images_grid, save_images
from utils.test_util import load_from_dataparallel_model, save_img, save_gan_img, save_obj_err_file
from utils.warp_ops import apply_disparity_cu
import torchvision.transforms as transforms


parser = argparse.ArgumentParser(description='Testing for CycleGAN + PSMNet')
parser.add_argument('--config-file', type=str, default='./configs/local_test.yaml',
                    metavar='FILE', help='Config files')
parser.add_argument('--model', type=str, default='', metavar='FILE', help='Path to test model')
parser.add_argument('--gan-model', type=str, default='', metavar='FILE', help='Path to test gan model')
parser.add_argument('--output', type=str, default='../testing_output_cyclegan_psmnet', help='Path to output folder')
parser.add_argument('--debug', action='store_true', default=False, help='Debug mode')
parser.add_argument('--annotate', type=str, default='', help='Annotation to the experiment')
parser.add_argument('--onreal', action='store_true', default=False, help='Test on real dataset')
parser.add_argument('--analyze-objects', action='store_true', default=True, help='Analyze on different objects')
parser.add_argument('--exclude-bg', action='store_true', default=False, help='Exclude background when testing')
parser.add_argument('--warp-op', action='store_true', default=True, help='Use warp_op function to get disparity')
parser.add_argument('--exclude-zeros', action='store_true', default=False, help='Whether exclude zero pixels in realsense')
parser.add_argument("--local_rank", type=int, default=0, help='Rank of device in distributed training')

args = parser.parse_args()
cfg.merge_from_file(args.config_file)
cuda_device = torch.device("cuda:{}".format(args.local_rank))
# If path to gan model is not specified, use gan model from cascade model
if args.gan_model == '':
    args.gan_model = args.model

# python test_cycleGAN_psmnet.py --model /code/models/model_4.pth --onreal --exclude-bg --exclude-zeros
# python test_cycleGAN_psmnet.py --config-file configs/remote_test.yaml --model ../train_8_14_cascade/train1/models/model_best.pth --onreal --exclude-bg --exclude-zeros --debug --gan-model


def test(gan_model, psmnet_model, feaex, val_loader, logger, log_dir, summary_writer):
    gan_model.eval()
    psmnet_model.eval()
    feaex.eval()
    psmnet_model.feature_extraction.gan_train = True

    total_err_metrics = {'epe': 0, 'bad1': 0, 'bad2': 0,
                         'depth_abs_err': 0, 'depth_err2': 0, 'depth_err4': 0, 'depth_err8': 0}
    total_obj_disp_err = np.zeros(cfg.SPLIT.OBJ_NUM)
    total_obj_depth_err = np.zeros(cfg.SPLIT.OBJ_NUM)
    total_obj_depth_4_err = np.zeros(cfg.SPLIT.OBJ_NUM)
    total_obj_count = np.zeros(cfg.SPLIT.OBJ_NUM)
    os.mkdir(os.path.join(log_dir, 'pred_disp'))
    os.mkdir(os.path.join(log_dir, 'gt_disp'))
    os.mkdir(os.path.join(log_dir, 'pred_disp_abs_err_cmap'))
    os.mkdir(os.path.join(log_dir, 'pred_depth'))
    os.mkdir(os.path.join(log_dir, 'gt_depth'))
    os.mkdir(os.path.join(log_dir, 'pred_depth_abs_err_cmap'))
    os.mkdir(os.path.join(log_dir, 'gan'))
    #os.mkdir(args.output + "/feature")

    for iteration, data in enumerate(tqdm(val_loader)):
        img_L = data['img_L'].cuda()    # [bs, 1, H, W]
        img_R = data['img_R'].cuda()

        img_disp_l = data['img_disp_l'].cuda()
        img_depth_l = data['img_depth_l'].cuda()
        img_depth_realsense = data['img_depth_realsense'].cuda()
        img_label = data['img_label'].cuda()
        img_focal_length = data['focal_length'].cuda()
        img_baseline = data['baseline'].cuda()
        prefix = data['prefix'][0]

        img_disp_l = F.interpolate(img_disp_l, (540, 960), mode='nearest',
                             recompute_scale_factor=False)
        img_depth_l = F.interpolate(img_depth_l, (540, 960), mode='nearest',
                             recompute_scale_factor=False)
        img_depth_realsense = F.interpolate(img_depth_realsense, (540, 960), mode='nearest',
                             recompute_scale_factor=False)
        img_label = F.interpolate(img_label, (540, 960), mode='nearest',
                             recompute_scale_factor=False).type(torch.int)

        # If using warp_op, computing img_disp_l from img_disp_r
        if args.warp_op:
            img_disp_r = data['img_disp_r'].cuda()
            img_depth_r = data['img_depth_r'].cuda()
            img_disp_r = F.interpolate(img_disp_r, (540, 960), mode='nearest',
                                       recompute_scale_factor=False)
            img_depth_r = F.interpolate(img_depth_r, (540, 960), mode='nearest',
                                        recompute_scale_factor=False)
            img_disp_l = apply_disparity_cu(img_disp_r, img_disp_r.type(torch.int))
            img_depth_l = apply_disparity_cu(img_depth_r, img_disp_r.type(torch.int))  # [bs, 1, H, W]

        # If test on real dataset need to crop input image to (540, 960)
        if not args.onreal:
            img_inputs = {
                'input': {
                    'input_L': img_L, 'input_R': img_R
                }
            }
            save_images_grid(summary_writer, 'test_gan', img_inputs, iteration)

            right_pad = cfg.REAL.PAD_WIDTH - 960
            top_pad = cfg.REAL.PAD_HEIGHT - 540
            img_L = F.pad(img_L, (0, right_pad, top_pad, 0, 0, 0, 0, 0), mode='constant', value=0)
            img_R = F.pad(img_R, (0, right_pad, top_pad, 0, 0, 0, 0, 0), mode='constant', value=0)
            img_L, img_R = feaex(img_L), feaex(img_R)
            
        else:  # If testing on sim, use GAN to generate img_L and img_R
            #img_L_real = data['img_L_real'].cuda()    # [bs, 1, H, W]
            #img_L_real = F.interpolate(img_L_real, (540, 960), mode='bilinear',
            #                      recompute_scale_factor=False, align_corners=False)
            img_L_o = F.interpolate(img_L, (540, 960), mode='bilinear',
                             recompute_scale_factor=False, align_corners=False)
            img_R_o = F.interpolate(img_R, (540, 960), mode='bilinear',
                             recompute_scale_factor=False, align_corners=False)
            
            right_pad = cfg.REAL.PAD_WIDTH - 960
            top_pad = cfg.REAL.PAD_HEIGHT - 540
            img_L = F.pad(img_L_o, (0, right_pad, top_pad, 0, 0, 0, 0, 0), mode='constant', value=0)
            img_R = F.pad(img_R_o, (0, right_pad, top_pad, 0, 0, 0, 0, 0), mode='constant', value=0)

            img_L, img_R = feaex(img_L), feaex(img_R)
            #print(img_L.shape)
            #trans = transforms.ToPILImage()
            #img_out_L = trans(img_L[0]).convert('RGB')
            #img_out_R = trans(img_R[0]).convert('RGB')
            
            #img_out_L.save(args.output + "/feature/" + f'{prefix}_L.png')
            #img_out_R.save(args.output + "/feature/" + f'{prefix}_R.png')
            
            input_sample = {'img_L': img_L, 'img_R': img_R, 'img_sim': img_R}
            gan_model.set_input(input_sample)
            with torch.no_grad():
                gan_model.forward()
                gan_model.compute_loss_G()
                img_L = gan_model.fake_B_L  # [bs, 1, H, W]
                img_R = gan_model.fake_B_R  # [bs, 1, H, W]

            # Save gan results
            #print(img_L_o.shape, img_R_o.shape, gan_model.real_A_L[:,0,:,:][:,None,:,:].shape, prefix)
            img_outputs = {
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
                }
            }
            img_inputs = {
                'input': {
                    'input_L': img_L_o, 'input_R': img_R_o
                }
            }
            #save_images_grid(summary_writer, 'test_gan', input_sample, iteration)
            save_images_grid(summary_writer, 'test_gan', img_inputs, iteration)
            save_images_grid(summary_writer, 'test_gan', img_outputs, iteration)
            save_gan_img(img_outputs, os.path.join(log_dir, 'gan', f'{prefix}.png'))

        # Pad the imput image and depth disp image to 960 * 544
        #right_pad = cfg.REAL.PAD_WIDTH - 960
        #top_pad = cfg.REAL.PAD_HEIGHT - 540
        #img_L = F.pad(img_L, (0, right_pad, top_pad, 0, 0, 0, 0, 0), mode='constant', value=0)
        #img_R = F.pad(img_R, (0, right_pad, top_pad, 0, 0, 0, 0, 0), mode='constant', value=0)

        if args.exclude_bg:
            print("bg flag is: ", args.exclude_bg)
            # Mask ground pixel to False
            img_ground_mask = (img_depth_l > 0) & (img_depth_l < 1.25)
            mask = (img_disp_l < cfg.ARGS.MAX_DISP) * (img_disp_l > 0) * img_ground_mask
        else:
            mask = (img_disp_l < cfg.ARGS.MAX_DISP) * (img_disp_l > 0)

        # Exclude uncertain pixel from realsense_depth_pred
        realsense_zeros_mask = img_depth_realsense > 0
        if args.exclude_zeros:
            print("zero flag is: ", args.exclude_zeros)
            mask = mask * realsense_zeros_mask
        mask = mask.type(torch.bool)

        ground_mask = torch.logical_not(mask).squeeze(0).squeeze(0).detach().cpu().numpy()

        with torch.no_grad():
            pred_disp = psmnet_model(img_L, img_R)
        pred_disp = pred_disp[:, :, top_pad:, :]  # TODO: if right_pad > 0 it needs to be (:-right_pad)
        pred_depth = img_focal_length * img_baseline / pred_disp  # pred depth in m

        # Get loss metric
        err_metrics = compute_err_metric(img_disp_l, img_depth_l, pred_disp, img_focal_length,
                                         img_baseline, mask)
        for k in total_err_metrics.keys():
            total_err_metrics[k] += err_metrics[k]
        logger.info(f'Test instance {prefix} - {err_metrics}')

        # Get object error
        obj_disp_err, obj_depth_err, obj_depth_4_err, obj_count = compute_obj_err(img_disp_l, img_depth_l, pred_disp, img_focal_length,
                                                     img_baseline, img_label, mask, cfg.SPLIT.OBJ_NUM)
        total_obj_disp_err += obj_disp_err
        total_obj_depth_err += obj_depth_err
        total_obj_depth_4_err += obj_depth_4_err
        total_obj_count += obj_count

        # Get disparity image
        pred_disp_np = pred_disp.squeeze(0).squeeze(0).detach().cpu().numpy()  # [H, W]
        pred_disp_np[ground_mask] = -1
        pred_disp_np_o = torch.tensor(pred_disp_np)[None,:,:]

        # Get disparity ground truth image
        gt_disp_np = img_disp_l.squeeze(0).squeeze(0).detach().cpu().numpy()
        gt_disp_np[ground_mask] = -1
        gt_disp_np_o = torch.tensor(gt_disp_np)[None,:,:]

        # Get disparity error image
        pred_disp_err_np = disp_error_img(pred_disp, img_disp_l, mask)
        #print("disperr: ", pred_disp_err_np.shape)

        # Get depth image
        pred_depth_np = pred_depth.squeeze(0).squeeze(0).detach().cpu().numpy()  # in m, [H, W]
        # crop depth map to [0.2m, 2m]
        # pred_depth_np[pred_depth_np < 0.2] = -1
        # pred_depth_np[pred_depth_np > 2] = -1
        pred_depth_np[ground_mask] = -1
        pred_depth_np_o = torch.tensor(pred_depth_np)[None,:,:]

        # Get depth ground truth image
        gt_depth_np = img_depth_l.squeeze(0).squeeze(0).detach().cpu().numpy()
        gt_depth_np[ground_mask] = -1
        gt_depth_np_o = torch.tensor(gt_depth_np)[None,:,:]
        #print('LOG shape: ', img_depth_l.shape, gt_depth_np.shape)

        # Get depth error image
        pred_depth_err_np = depth_error_img(pred_depth * 1000, img_depth_l * 1000, mask)
        #print("deperr: ", pred_depth_err_np.shape)

        # Save images
        image_test_output = {'pred_disp': pred_disp_np_o, 'gt_disp': gt_disp_np_o, 'pred_depth': pred_depth_np_o, 'gt_depth': gt_depth_np_o}
        save_images(summary_writer, 'test_psmnet', image_test_output, iteration)
        save_img(log_dir, prefix, pred_disp_np, gt_disp_np, pred_disp_err_np,
                 pred_depth_np, gt_depth_np, pred_depth_err_np)

    # Get final error metrics
    for k in total_err_metrics.keys():
        total_err_metrics[k] /= len(val_loader)
    logger.info(f'\nTest on {len(val_loader)} instances\n {total_err_metrics}')

    # Save object error to csv file
    total_obj_disp_err /= total_obj_count
    total_obj_depth_err /= total_obj_count
    total_obj_depth_4_err /= total_obj_count
    save_obj_err_file(total_obj_disp_err, total_obj_depth_err, total_obj_depth_4_err, log_dir)

    logger.info(f'Successfully saved object error to obj_err.txt')


def main():
    # Obtain the dataloader
    val_loader = get_test_loader(cfg.REAL.TRAIN, args.debug, sub=40, onReal=args.onreal)

    # Tensorboard and logger
    os.makedirs(args.output, exist_ok=True)
    log_dir = os.path.join(args.output, f'{get_time_string()}_{args.annotate}')
    os.mkdir(log_dir)
    logger = setup_logger("CycleGAN-PSMNet Testing", distributed_rank=0, save_dir=log_dir)
    logger.info(f'Annotation: {args.annotate}')
    logger.info(f'Input args {args}')
    logger.info(f'Loaded config file \'{args.config_file}\'')
    logger.info(f'Running with configs:\n{cfg}')


    # Get GAN model
    gan_model = CycleGANModel()
    gan_model.set_device(cuda_device)
    gan_model.load_model(args.gan_model)

    # Get cascade model
    logger.info(f'Loaded the checkpoint: {args.model}')
    summary_writer = tensorboardX.SummaryWriter(logdir=args.output)
    psmnet_model = PSMNet(maxdisp=cfg.ARGS.MAX_DISP).to(cuda_device)
    model_dict = load_from_dataparallel_model(args.model, 'PSMNet')
    psmnet_model.load_state_dict(model_dict)

    feaex = psmnet_model.feature_extraction.ganfeature
    psmnet_model.feature_extraction.gan_train = False

    test(gan_model, psmnet_model, feaex, val_loader, logger, log_dir, summary_writer)


if __name__ == '__main__':
    main()