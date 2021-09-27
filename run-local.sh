#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /code/train_psm.py \
--config-file /code/configs/local_train_gan.yaml --summary-freq 5 \
--save-freq 100 --logdir /data/logs/GANPSMFeature_image2 #--loadmodel "/cephfs/jianyu/eval/psm_eval/checkpoint_0.tar"
