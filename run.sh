#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /cephfs/jianyu/GANPSMFeature/train_cycleGAN_psm.py --config-file /cephfs/jianyu/GANPSMFeature/configs/remote_train_gan.yaml --summary-freq 10 --save-freq 2000 --logdir /cephfs/jianyu/eval/GANPSMFeature_3channel --loadmodel "/cephfs/jianyu/eval/ganpsm_pre_f/models/model_best.pth"
