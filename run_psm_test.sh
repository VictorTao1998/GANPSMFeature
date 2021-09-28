#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /cephfs/jianyu/GANPSMFeature/test_psm.py \
--config-file /cephfs/jianyu/GANPSMFeature/configs/remote_train_gan.yaml --summary-freq 1 --save-freq 100 --logdir /cephfs/jianyu/eval/psm_deep_test \
--loadmodel /cephfs/jianyu/eval/psm_deep_train/models/model_best.pth
