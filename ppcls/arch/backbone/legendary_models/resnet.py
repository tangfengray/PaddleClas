# copyright (c) 2021 PaddlePaddle Authors. All Rights Reserve.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import, division, print_function

import numpy as np
import paddle
from paddle import ParamAttr
import paddle.nn as nn
from paddle.nn import Conv2D, BatchNorm, Linear
from paddle.nn import AdaptiveAvgPool2D, MaxPool2D, AvgPool2D
from paddle.nn.initializer import Uniform
import math

from theseus_layer import TheseusLayer
from ppcls.utils.save_load import load_dygraph_pretrain_from, load_dygraph_pretrain_from_url


MODEL_URLS = {
    "ResNet18": "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/ResNet18_pretrained.pdparams",
    "ResNet18_vd": "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/ResNet18_vd_pretrained.pdparams",
    "ResNet34": "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/ResNet34_pretrained.pdparams",
    "ResNet34_vd": "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/ResNet34_vd_pretrained.pdparams",
    "ResNet50": "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/ResNet50_pretrained.pdparams",
    "ResNet50_vd": "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/ResNet50_vd_pretrained.pdparams",
    "ResNet101": "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/ResNet101_pretrained.pdparams",
    "ResNet101_vd": "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/ResNet101_vd_pretrained.pdparams",
    "ResNet152": "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/ResNet152_pretrained.pdparams",
    "ResNet152_vd": "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/ResNet152_vd_pretrained.pdparams",
    "ResNet200_vd": "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/ResNet200_vd_pretrained.pdparams",
}

__all__ = MODEL_URLS.keys()


'''
ResNet config: dict.
    key: depth of ResNet.
    values: config's dict of specific model.
        keys:
            block_type: Two different blocks in ResNet, BasicBlock and BottleneckBlock are optional.
            block_depth: The number of blocks in different stages in ResNet.
            num_channels: The number of channels to enter the next stage.
'''
NET_CONFIG = {
    "18": {
        "block_type": "BasicBlock", "block_depth": [2, 2, 2, 2], "num_channels": [64, 64, 128, 256]},
    "34": {
        "block_type": "BasicBlock", "block_depth": [3, 4, 6, 3], "num_channels": [64, 64, 128, 256]},
    "50": {
        "block_type": "BottleneckBlock", "block_depth": [3, 4, 6, 3], "num_channels": [64, 256, 512, 1024]},
    "101": {
        "block_type": "BottleneckBlock", "block_depth": [3, 4, 23, 3], "num_channels": [64, 256, 512, 1024]},
    "152": {
        "block_type": "BottleneckBlock", "block_depth": [3, 8, 36, 3], "num_channels": [64, 256, 512, 1024]},
    "200": {
        "block_type": "BottleneckBlock", "block_depth": [3, 12, 48, 3], "num_channels": [64, 256, 512, 1024]},
}


class ConvBNLayer(TheseusLayer):
    def __init__(self,
                 num_channels,
                 num_filters,
                 filter_size,
                 stride=1,
                 groups=1,
                 is_vd_mode=False,
                 act=None,
                 lr_mult=1.0):
        super().__init__()
        self.is_vd_mode = is_vd_mode
        self.act = act
        self.avgpool = AvgPool2D(
            kernel_size=2, stride=2, padding=0, ceil_mode=True)
        self.conv = Conv2D(
            in_channels=num_channels,
            out_channels=num_filters,
            kernel_size=filter_size,
            stride=stride,
            padding=(filter_size - 1) // 2,
            groups=groups,
            weight_attr=ParamAttr(learning_rate=lr_mult),
            bias_attr=False)
        self.bn = BatchNorm(
            num_filters,
            param_attr=ParamAttr(learning_rate=lr_mult),
            bias_attr=ParamAttr(learning_rate=lr_mult))
        self.relu = nn.ReLU()

    def forward(self, x):
        if self.is_vd_mode:
            x = self.avgpool(x)
        x = self.conv(x)
        x = self.bn(x)
        if self.act:
            x = self.relu(x)
        return x


class BottleneckBlock(TheseusLayer):
    def __init__(self,
                 num_channels,
                 num_filters,
                 stride,
                 shortcut=True,
                 if_first=False,
                 lr_mult=1.0,
                ):
        super().__init__()

        self.conv0 = ConvBNLayer(
            num_channels=num_channels,
            num_filters=num_filters,
            filter_size=1,
            act="relu",
            lr_mult=lr_mult)
        self.conv1 = ConvBNLayer(
            num_channels=num_filters,
            num_filters=num_filters,
            filter_size=3,
            stride=stride,
            act="relu",
            lr_mult=lr_mult)
        self.conv2 = ConvBNLayer(
            num_channels=num_filters,
            num_filters=num_filters * 4,
            filter_size=1,
            act=None,
            lr_mult=lr_mult)

        if not shortcut:
            self.short = ConvBNLayer(
                num_channels=num_channels,
                num_filters=num_filters * 4,
                filter_size=1,
                stride=stride if if_first else 1,
                is_vd_mode=False if if_first else True,
                lr_mult=lr_mult)
        self.relu = nn.ReLU()
        self.shortcut = shortcut

    def forward(self, x):
        identity = x
        x = self.conv0(x)
        x = self.conv1(x)
        x = self.conv2(x)

        if self.shortcut:
            short = identity
        else:
            short = self.short(identity)
        x = paddle.add(x=x, y=short)
        x = self.relu(x)
        return x


class BasicBlock(TheseusLayer):
    def __init__(self,
                 num_channels,
                 num_filters,
                 stride,
                 shortcut=True,
                 if_first=False,
                 lr_mult=1.0):
        super().__init__()

        self.stride = stride
        self.conv0 = ConvBNLayer(
            num_channels=num_channels,
            num_filters=num_filters,
            filter_size=3,
            stride=stride,
            act="relu",
            lr_mult=lr_mult)
        self.conv1 = ConvBNLayer(
            num_channels=num_filters,
            num_filters=num_filters,
            filter_size=3,
            act=None,
            lr_mult=lr_mult)
        if not shortcut:
            self.short = ConvBNLayer(
                num_channels=num_channels,
                num_filters=num_filters,
                filter_size=1,
                stride=stride if if_first else 1,
                is_vd_mode=False if if_first else True,
                lr_mult=lr_mult)
        self.shortcut = shortcut
        self.relu = nn.ReLU()

    def forward(self, x):
        identity = x
        x = self.conv0(x)
        x = self.conv1(x)
        if self.shortcut:
            short = identity
        else:
            short = self.short(identity)
        x = paddle.add(x=x, y=short)
        x = self.relu(x)
        return x


class ResNet(TheseusLayer):
    """
    ResNet
    Args:
        config: dict. config of ResNet.
        version: str="vb". Different version of ResNet, version vd can perform better. 
        class_num: int=1000. The number of classes.
        lr_mult_list: list. Control the learning rate of different stages.
        pretrained: (True or False) or path of pretrained_model. Whether to load the pretrained model.
    Returns:
        model: nn.Layer. Specific ResNet model depends on args.
    """
    def __init__(self,
                 config,
                 version="vb",
                 class_num=1000,
                 lr_mult_list=[1.0, 1.0, 1.0, 1.0, 1.0],
                 pretrained=False):
        super().__init__()

        self.cfg = config
        self.lr_mult_list = lr_mult_list
        self.is_vd_mode = version == "vd"
        self.class_num = class_num
        self.num_filters = [64, 128, 256, 512]
        self.block_depth = self.cfg["block_depth"]
        self.block_type = self.cfg["block_type"]
        self.num_channels = self.cfg["num_channels"]
        self.channels_mult = 1 if self.num_channels[-1] == 256 else 4
        self.pretrained = pretrained   
     
        assert isinstance(self.lr_mult_list, (
            list, tuple
        )), "lr_mult_list should be in (list, tuple) but got {}".format(
            type(self.lr_mult_list))
        assert len(
            self.lr_mult_list
        ) == 5, "lr_mult_list length should be 5 but got {}".format(
            len(self.lr_mult_list))
        

        self.stem_cfg = {
            #num_channels, num_filters, filter_size, stride
            "vb": [[3, 64, 7, 2]],
            "vd": [[3, 32, 3, 2],
                   [32, 32, 3, 1],
                   [32, 64, 3, 1]]}
        
        self.stem = nn.Sequential(*[
            ConvBNLayer(
                    num_channels=in_c,
                    num_filters=out_c,
                    filter_size=k,
                    stride=s,
                    act="relu",
                    lr_mult=self.lr_mult_list[0])
            for in_c, out_c, k, s in self.stem_cfg[version]
        ])
        
        self.maxpool = MaxPool2D(kernel_size=3, stride=2, padding=1)
        block_list = []
        for block_idx in range(len(self.block_depth)):
            shortcut = False
            for i in range(self.block_depth[block_idx]):
                block_list.append(
                    globals()[self.block_type](
                    num_channels=self.num_channels[block_idx]
                    if i == 0 else self.num_filters[block_idx] * self.channels_mult,
                    num_filters=self.num_filters[block_idx],
                    stride=2 if i == 0 and block_idx != 0 else 1,
                    shortcut=shortcut,
                    if_first=block_idx == i == 0 if version == "vd" else True,
                    lr_mult=self.lr_mult_list[block_idx + 1]))
                shortcut = True    
        self.blocks = nn.Sequential(*block_list)

        self.avgpool = AdaptiveAvgPool2D(1)
        self.avgpool_channels = self.num_channels[-1] * 2

        stdv = 1.0 / math.sqrt(self.avgpool_channels * 1.0)
        self.out = Linear(
            self.avgpool_channels,
            self.class_num,
            weight_attr=ParamAttr(
                initializer=Uniform(-stdv, stdv)))

    def forward(self, x):
        x = self.stem(x)
        x = self.maxpool(x)
        x = self.blocks(x)
        x = self.avgpool(x)
        x = paddle.reshape(x, shape=[-1, self.avgpool_channels])
        x = self.out(x)
        return x


def ResNet18(**args):
    """
    ResNet18
    Args:
        kwargs: 
            class_num: int=1000. Output dim of last fc layer.
            lr_mult_list: list=[1.0, 1.0, 1.0, 1.0, 1.0]. Control the learning rate of different stages.
            pretrained: bool or str, default: bool=False. Whether to load the pretrained model.
    Returns:
        model: nn.Layer. Specific `ResNet18` model depends on args.
    """
    model = ResNet(config=NET_CONFIG["18"], version="vb", **args)
    if isinstance(model.pretrained, bool):
        if model.pretrained is True:
            load_dygraph_pretrain_from_url(model, MODEL_URLS["ResNet18"])
    elif isinstance(model.pretrained, str):
        load_dygraph_pretrain(model, model.pretrained)
    else:
        raise RuntimeError(
            "pretrained type is not available. Please use `string` or `boolean` type")
    return model


def ResNet18_vd(**args):
    """
    ResNet18_vd
    Args:
        kwargs: 
            class_num: int=1000. Output dim of last fc layer.
            lr_mult_list: list=[1.0, 1.0, 1.0, 1.0, 1.0]. Control the learning rate of different stages.
            pretrained: bool or str, default: bool=False. Whether to load the pretrained model.
    Returns:
        model: nn.Layer. Specific `ResNet18_vd` model depends on args.
    """
    model = ResNet(config=NET_CONFIG["18"], version="vd", **args)
    if isinstance(model.pretrained, bool):
        if model.pretrained is True:
            load_dygraph_pretrain_from_url(model, MODEL_URLS["ResNet18_vd"])
    elif isinstance(model.pretrained, str):
        load_dygraph_pretrain(model, model.pretrained)
    else:
        raise RuntimeError(
            "pretrained type is not available. Please use `string` or `boolean` type")
    return model


def ResNet34(**args):
    """
    ResNet34
    Args:
        kwargs: 
            class_num: int=1000. Output dim of last fc layer.
            lr_mult_list: list=[1.0, 1.0, 1.0, 1.0, 1.0]. Control the learning rate of different stages.
            pretrained: bool or str, default: bool=False. Whether to load the pretrained model.
    Returns:
        model: nn.Layer. Specific `ResNet18` model depends on args.
    """
    model = ResNet(config=NET_CONFIG["34"], version="vb", **args)
    if isinstance(model.pretrained, bool):
        if model.pretrained is True:
            load_dygraph_pretrain_from_url(model, MODEL_URLS["ResNet34"])
    elif isinstance(model.pretrained, str):
        load_dygraph_pretrain(model, model.pretrained)
    else:
        raise RuntimeError(
            "pretrained type is not available. Please use `string` or `boolean` type")
    return model


def ResNet34_vd(**args):
    """
    ResNet34_vd
    Args:
        kwargs: 
            class_num: int=1000. Output dim of last fc layer.
            lr_mult_list: list=[1.0, 1.0, 1.0, 1.0, 1.0]. Control the learning rate of different stages.
            pretrained: bool or str, default: bool=False. Whether to load the pretrained model.
    Returns:
        model: nn.Layer. Specific `ResNet18_vd` model depends on args.
    """
    model = ResNet(config=NET_CONFIG["34"], version="vd", **args)
    if isinstance(model.pretrained, bool):
        if model.pretrained is True:
            load_dygraph_pretrain_from_url(model, MODEL_URLS["ResNet34_vd"], use_ssld=True)
    elif isinstance(model.pretrained, str):
        load_dygraph_pretrain(model, model.pretrained)
    else:
        raise RuntimeError(
            "pretrained type is not available. Please use `string` or `boolean` type")
    return model


def ResNet50(**args):
    """
    ResNet50
    Args:
        kwargs: 
            class_num: int=1000. Output dim of last fc layer.
            lr_mult_list: list=[1.0, 1.0, 1.0, 1.0, 1.0]. Control the learning rate of different stages.
            pretrained: bool or str, default: bool=False. Whether to load the pretrained model.
    Returns:
        model: nn.Layer. Specific `ResNet50` model depends on args.
    """
    model = ResNet(config=NET_CONFIG["50"], version="vb", **args)
    if isinstance(model.pretrained, bool):
        if model.pretrained is True:
            load_dygraph_pretrain_from_url(model, MODEL_URLS["ResNet50"])
    elif isinstance(model.pretrained, str):
        load_dygraph_pretrain(model, model.pretrained)
    else:
        raise RuntimeError(
            "pretrained type is not available. Please use `string` or `boolean` type")
    return model


def ResNet50_vd(**args):
    """
    ResNet50_vd
    Args:
        kwargs: 
            class_num: int=1000. Output dim of last fc layer.
            lr_mult_list: list=[1.0, 1.0, 1.0, 1.0, 1.0]. Control the learning rate of different stages.
            pretrained: bool or str, default: bool=False. Whether to load the pretrained model.
    Returns:
        model: nn.Layer. Specific `ResNet50_vd` model depends on args.
    """
    model = ResNet(config=NET_CONFIG["50"], version="vd", **args)
    if isinstance(model.pretrained, bool):
        if model.pretrained is True:
            load_dygraph_pretrain_from_url(model, MODEL_URLS["ResNet50_vd"], use_ssld=True)
    elif isinstance(model.pretrained, str):
        load_dygraph_pretrain(model, model.pretrained)
    else:
        raise RuntimeError(
            "pretrained type is not available. Please use `string` or `boolean` type")
    return model


def ResNet101(**args):
    """
    ResNet101
    Args:
        kwargs: 
            class_num: int=1000. Output dim of last fc layer.
            lr_mult_list: list=[1.0, 1.0, 1.0, 1.0, 1.0]. Control the learning rate of different stages.
            pretrained: bool=False. Whether to load the pretrained model.
    Returns:
        model: nn.Layer. Specific `ResNet101` model depends on args.
    """
    model = ResNet(config=NET_CONFIG["101"], version="vb", **args)
    if isinstance(model.pretrained, bool):
        if model.pretrained is True:
            load_dygraph_pretrain_from_url(model, MODEL_URLS["ResNet101"])
    elif isinstance(model.pretrained, str):
        load_dygraph_pretrain(model, model.pretrained)
    else:
        raise RuntimeError(
            "pretrained type is not available. Please use `string` or `boolean` type")
    return model


def ResNet101_vd(**args):
    """
    ResNet101_vd
    Args:
        kwargs: 
            class_num: int=1000. Output dim of last fc layer.
            lr_mult_list: list=[1.0, 1.0, 1.0, 1.0, 1.0]. Control the learning rate of different stages.
            pretrained: bool or str, default: bool=False. Whether to load the pretrained model.
    Returns:
        model: nn.Layer. Specific `ResNet101_vd` model depends on args.
    """
    model = ResNet(config=NET_CONFIG["101"], version="vd", **args)
    if isinstance(model.pretrained, bool):
        if model.pretrained is True:
            load_dygraph_pretrain_from_url(model, MODEL_URLS["ResNet101_vd"], use_ssld=True)
    elif isinstance(model.pretrained, str):
        load_dygraph_pretrain(model, model.pretrained)
    else:
        raise RuntimeError(
            "pretrained type is not available. Please use `string` or `boolean` type")
    return model


def ResNet152(**args):
    """
    ResNet152
    Args:
        kwargs: 
            class_num: int=1000. Output dim of last fc layer.
            lr_mult_list: list=[1.0, 1.0, 1.0, 1.0, 1.0]. Control the learning rate of different stages.
            pretrained: bool or str, default: bool=False. Whether to load the pretrained model.
    Returns:
        model: nn.Layer. Specific `ResNet152` model depends on args.
    """
    model = ResNet(config=NET_CONFIG["152"], version="vb", **args)
    if isinstance(model.pretrained, bool):
        if model.pretrained is True:
            load_dygraph_pretrain_from_url(model, MODEL_URLS["ResNet152"])
    elif isinstance(model.pretrained, str):
        load_dygraph_pretrain(model, model.pretrained)
    else:
        raise RuntimeError(
            "pretrained type is not available. Please use `string` or `boolean` type")
    return model


def ResNet152_vd(**args):
    """
    ResNet152_vd
    Args:
        kwargs: 
            class_num: int=1000. Output dim of last fc layer.
            lr_mult_list: list=[1.0, 1.0, 1.0, 1.0, 1.0]. Control the learning rate of different stages.
            pretrained: bool or str, default: bool=False. Whether to load the pretrained model.
    Returns:
        model: nn.Layer. Specific `ResNet152_vd` model depends on args.
    """
    model = ResNet(config=NET_CONFIG["152"], version="vd", **args)
    if isinstance(model.pretrained, bool):
        if model.pretrained is True:
            load_dygraph_pretrain_from_url(model, MODEL_URLS["ResNet152_vd"])
    elif isinstance(model.pretrained, str):
        load_dygraph_pretrain(model, model.pretrained)
    else:
        raise RuntimeError(
            "pretrained type is not available. Please use `string` or `boolean` type")
    return model


def ResNet200_vd(**args):
    """
    ResNet200_vd
    Args:
        kwargs: 
            class_num: int=1000. Output dim of last fc layer.
            lr_mult_list: list=[1.0, 1.0, 1.0, 1.0, 1.0]. Control the learning rate of different stages.
            pretrained: bool or str, default: bool=False. Whether to load the pretrained model.
    Returns:
        model: nn.Layer. Specific `ResNet200_vd` model depends on args.
    """
    model = ResNet(config=NET_CONFIG["200"], version="vd", **args)
    if isinstance(model.pretrained, bool):
        if model.pretrained is True:
            load_dygraph_pretrain_from_url(model, MODEL_URLS["ResNet200_vd"], use_ssld=True)
    elif isinstance(model.pretrained, str):
        load_dygraph_pretrain(model, model.pretrained)
    else:
        raise RuntimeError(
            "pretrained type is not available. Please use `string` or `boolean` type")
    return model
