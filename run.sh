#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /cephfs/jianyu/GANPSMFeature/train_cycleGAN_psm.py --config-file /cephfs/jianyu/GANPSMFeature/configs/remote_train_gan.yaml --summary-freq 100 --save-freq 2000 --logdir /cephfs/jianyu/eval/GANPSMFeature_deep --loadmodel "/cephfs/jianyu/eval/psm_deep_train/models/model_best.pth"
