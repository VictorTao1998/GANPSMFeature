#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /code/train_finetune_psm.py \
--config-file /code/configs/remote_train_gan.yaml --summary-freq 1000 \
--save-freq 100 --logdir /data/eval/psm_deep_train \
--loadmodel "/cephfs/jianyu/eval/psm_deep_train/models/model_best.pth"
