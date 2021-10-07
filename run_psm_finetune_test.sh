#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /cephfs/jianyu/GANPSMFeature/test_psm_finetune.py \
--config-file /cephfs/jianyu/GANPSMFeature/configs/remote_test_gan.yaml --output /cephfs/jianyu/eval/psm_finetune_test_ori \
--model /cephfs/jianyu/eval/psm_deep_train_finetune_ori/models/model_best.pth \
--exclude-bg --exclude-zeros --onreal