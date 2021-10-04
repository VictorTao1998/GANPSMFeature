#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /cephfs/jianyu/GANPSMFeature/test_cycleGAN_psm.py \
--config-file /cephfs/jianyu/GANPSMFeature/configs/remote_test_gan.yaml --output /cephfs/jianyu/eval/ganpsm_test_deep_include_sim \
--model /cephfs/jianyu/eval/ganpsm_trains_deep/models/model_19999.pth --gan-model /cephfs/jianyu/eval/GANPSMFeature_deep/models/model_49999.pth \
--exclude-bg --exclude-zeros --onreal