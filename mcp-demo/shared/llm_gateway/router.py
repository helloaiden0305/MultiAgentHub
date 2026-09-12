"""
模型路由策略 — 将逻辑模型名解析为实际调用参数。

路由逻辑：
  "auto"        → 使用 config.yaml 中配置的默认模型
  "doubao-pro"  → 直接使用该模型配置
  "doubao-lite" → 直接使用该模型配置
"""

# PyYAML：用于读取 config.yaml 配置文件，将 YAML 格式解析为 Python dict
import yaml
# pathlib.Path：用于跨平台的文件路径拼接，定位同目录下的 config.yaml
from pathlib import Path


class ModelRouter:
    """根据配置将逻辑模型名映射到实际调用参数。

    职责：
    - 加载 config.yaml 中定义的模型列表和路由规则
    - 将 "auto" 解析为配置中的默认模型名
    - 校验模型名合法性，拒绝未注册的模型
    - 提供模型配置查询（max_tokens、temperature 等）
    """

    def __init__(self, config_path: str | Path | None = None):
        """初始化路由器，加载模型配置。

        Args:
            config_path: 配置文件路径。默认 None 时自动定位到同目录下的 config.yaml。
                         也可传入自定义路径，方便测试时用不同配置。
        """
        # 如果没有指定配置路径，默认用与本文件同目录下的 config.yaml
        # Path(__file__).parent 获取 router.py 所在目录（shared/llm_gateway/）
        if config_path is None:
            config_path = Path(__file__).parent / "config.yaml"

        # 读取 YAML 配置文件并解析为 Python dict
        # encoding="utf-8" 确保中文描述不会乱码
        with open(config_path, encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        # 提取模型定义字典，结构如：
        # {"doubao-pro": {"description": "...", "max_tokens": 4096, ...},
        #  "doubao-lite": {"description": "...", "max_tokens": 2048, ...}}
        self.models: dict = self.config["models"]

        # 提取默认模型名，即 model="auto" 时实际使用的模型
        # 对应 config.yaml 中的 routing.default 字段
        self.default_model: str = self.config["routing"]["default"]

    def resolve(self, model: str = "auto") -> str:
        """将逻辑模型名解析为最终使用的模型标识。

        这是路由的核心方法：
        - "auto" → 替换为 config.yaml 中配置的默认模型（如 "doubao-pro"）
        - "doubao-pro" / "doubao-lite" → 原样返回（直接匹配）
        - 其他未注册的名字 → 抛出 ValueError

        Args:
            model: 调用方传入的模型名。默认 "auto"。

        Returns:
            解析后的实际模型名（如 "doubao-pro"）。

        Raises:
            ValueError: 模型名不在 config.yaml 的 models 中。
        """
        # "auto" 是语法糖，替换为配置的默认值
        if model == "auto":
            model = self.default_model

        # 校验模型名是否已在 config.yaml 中注册
        # 如果传入了 "gpt-4" 之类未注册的名字，在这里拦截
        if model not in self.models:
            available = ", ".join(self.models.keys())
            raise ValueError(f"未知模型 '{model}'，可选: {available}")

        return model

    def get_model_config(self, model: str) -> dict:
        """获取指定模型的完整配置（含 name 字段）。

        先调用 resolve() 解析模型名，再从 config 中取出该模型的全部参数。
        返回结果额外加上 "name" 字段，方便调用方一次性拿到名字+配置。

        Args:
            model: 逻辑模型名（"auto" / "doubao-pro" / "doubao-lite"）。

        Returns:
            如 {"name": "doubao-pro", "description": "...", "max_tokens": 4096, "temperature": 0.7}
        """
        # 先解析 "auto" → 实际模型名
        resolved = self.resolve(model)
        # 合并 name 和 config.yaml 中该模型的所有字段
        # **self.models[resolved] 展开 {"description": "...", "max_tokens": 4096, ...}
        return {"name": resolved, **self.models[resolved]}

    def list_models(self) -> list[dict]:
        """列出所有可用模型及其配置信息。

        遍历 config.yaml 中 models 下的每一项，附加 is_default 标记。
        供 list_models Tool 使用，让调用方知道有哪些模型可选。

        Returns:
            如 [{"name": "doubao-pro", "is_default": True, "description": "...", ...},
                {"name": "doubao-lite", "is_default": False, "description": "...", ...}]
        """
        return [
            # 对每个模型：name=模型名, is_default=是否为默认模型, **cfg=展开其余配置字段
            {"name": name, "is_default": name == self.default_model, **cfg}
            for name, cfg in self.models.items()
        ]
