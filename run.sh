#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /cephfs/jianyu/GANPSMFeature/train_cycleGAN_psm.py --config-file /cephfs/jianyu/GANPSMFeature/configs/remote_train_gan.yaml --summary-freq 10 --save-freq 100 --logdir /cephfs/jianyu/eval/GANPSMFeature_image3 --loadmodel "/cephfs/jianyu/eval/psm_eval/checkpoint_0.tar"
