#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /cephfs/jianyu/GANPSMFeature/test_cycleGAN_psm.py \
--config-file /cephfs/jianyu/GANPSMFeature/configs/remote_test_gan.yaml --output /cephfs/jianyu/eval/ganpsm_test_normal \
--model /cephfs/jianyu/eval/GANPSMFeature_normal/models/model_13999.pth --gan-model /cephfs/jianyu/eval/GANPSMFeature_normal/models/model_13999.pth \
--exclude-bg --onreal
