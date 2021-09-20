#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /code/GANPSMFeature/train_psm.py \
--config-file /code/GANPSMFeature/configs/local_train_gan.yaml --summary-freq 10 \
--save-freq 100 --logdir /data/logs/GANPSMFeature_image2 --summary-freq 5 #--loadmodel "/cephfs/jianyu/eval/psm_eval/checkpoint_0.tar"
