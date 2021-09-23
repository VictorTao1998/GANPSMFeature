#!/bin/bash
export PYTHONWARNINGS="ignore"

python -m torch.distributed.launch /cephfs/jianyu/GANPSMFeature/test_cycleGAN_psm.py --config-file /cephfs/jianyu/GANPSMFeature/configs/remote_train_gan.yaml --summary-freq 100 --save-freq 100 --output /cephfs/jianyu/eval/ganpsm_test --model /cephfs/jianyu/eval/GANPSMFeature_joint/models/model_9999.pth --gan_model /cephfs/jianyu/eval/GANPSMFeature_joint/models/model_9999.pth
