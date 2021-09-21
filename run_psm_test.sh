#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /cephfs/jianyu/GANPSMFeature/test_psm.py \
--config-file /cephfs/jianyu/GANPSMFeature/configs/remote_train_gan.yaml --summary-freq 1 --save-freq 100 --logdir /cephfs/jianyu/eval/psm_test \
--loadmodel /cephfs/jianyu/eval/ganpsm_pre_f/models/model_best.pth
