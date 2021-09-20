#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /cephfs/jianyu/GANPSMFeature/train_psm.py --config-file /cephfs/jianyu/GANPSMFeature/configs/remote_train_gan.yaml --summary-freq 500 --save-freq 100 --logdir /cephfs/jianyu/eval/ganpsm_pre3
