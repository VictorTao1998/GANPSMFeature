#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /cephfs/jianyu/GANPSMFeature/train_psm.py --config-file /cephfs/jianyu/GANPSMFeature/configs/remote_train_gan.yaml --summary-freq 1000 --save-freq 100 --logdir /cephfs/jianyu/eval/psm_deep_train
