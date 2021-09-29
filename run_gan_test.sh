#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /cephfs/jianyu/GANPSMFeature/test_cycleGAN_psm.py \
--config-file /cephfs/jianyu/GANPSMFeature/configs/remote_test_gan.yaml --output /cephfs/jianyu/eval/ganpsm_test_deep \
--model /cephfs/jianyu/eval/GANPSMFeature_deep/models/model_15999.pth --gan-model /cephfs/jianyu/eval/GANPSMFeature_deep/models/model_15999.pth \
--exclude-bg --exclude-zeros --onreal 
