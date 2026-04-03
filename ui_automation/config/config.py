import json
import os
import re

import yaml
from dotenv import load_dotenv

from ui_automation.utils import print_with_color


def _resolve_env_vars(data):
    """Recursively resolve ${VAR} placeholders in YAML values."""
    if isinstance(data, dict):
        return {k: _resolve_env_vars(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_resolve_env_vars(i) for i in data]
    elif isinstance(data, str):
        def replacer(m):
            return os.environ.get(m.group(1), m.group(0))
        return re.sub(r"\$\{([^}]+)\}", replacer, data)
    return data


class Config:
    _instance = None

    def __init__(self):
        if os.getenv("RUN_CONFIGS", "true").lower() != "false":
            self.config_data = self.load_config()
        else:
            self.config_data = None

    @staticmethod
    def get_instance():
        if Config._instance is None:
            Config._instance = Config()
        return Config._instance

    def load_config(self, config_path="ui_automation/config/") -> dict:
        load_dotenv()
        os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
        configs = dict(os.environ)

        path = config_path

        try:
            with open(path + "config.yaml", "r") as file:
                yaml_data = yaml.safe_load(file)
            if yaml_data:
                yaml_data = _resolve_env_vars(yaml_data)
                configs.update(yaml_data)
            if os.path.exists(path + "config_dev.yaml"):
                with open(path + "config_dev.yaml", "r") as file:
                    yaml_dev_data = yaml.safe_load(file)
                if yaml_dev_data:
                    yaml_dev_data = _resolve_env_vars(yaml_dev_data)
                    configs.update(yaml_dev_data)
            if os.path.exists(path + "config_prices.yaml"):
                with open(path + "config_prices.yaml", "r") as file:
                    yaml_prices_data = yaml.safe_load(file)
                if yaml_prices_data:
                    configs.update(yaml_prices_data)
        except FileNotFoundError:
            print_with_color(
                f"Предупреждение: файл конфигурации не найден по пути {config_path}. Будут использованы только переменные окружения.",
                "yellow",
            )

        return self.optimize_configs(configs)

    @staticmethod
    def update_api_base(configs: dict, agent: str) -> None:
        if agent not in configs:
            print_with_color(
                f"Предупреждение: агент {agent} не найден в конфигурациях.",
                "yellow",
            )
            return

        if configs[agent]["API_TYPE"].lower() == "aoai":
            if "deployments" not in configs[agent]["API_BASE"]:
                configs[agent]["API_BASE"] = (
                    "{endpoint}/openai/deployments/{deployment_name}/chat/completions?api-version={api_version}".format(
                        endpoint=(
                            configs[agent]["API_BASE"][:-1]
                            if configs[agent]["API_BASE"].endswith("/")
                            else configs[agent]["API_BASE"]
                        ),
                        deployment_name=configs[agent]["API_DEPLOYMENT_ID"],
                        api_version=configs[agent]["API_VERSION"],
                    )
                )
            configs[agent]["API_MODEL"] = configs[agent]["API_DEPLOYMENT_ID"]
        elif configs[agent]["API_TYPE"].lower() == "openai":
            if "chat/completions" in configs[agent]["API_BASE"]:
                configs[agent]["API_BASE"] = (
                    configs[agent]["API_BASE"][:-18]
                    if configs[agent]["API_BASE"].endswith("/")
                    else configs[agent]["API_BASE"][:-17]
                )

    @classmethod
    def optimize_configs(cls, configs: dict) -> dict:
        cls.update_api_base(configs, "HOST_AGENT")
        cls.update_api_base(configs, "APP_AGENT")
        cls.update_api_base(configs, "BACKUP_AGENT")

        if isinstance(configs.get("CONTROL_BACKEND"), str):
            configs["CONTROL_BACKEND"] = [configs["CONTROL_BACKEND"]]

        return configs


def get_offline_learner_indexer_config():
    file_path = "learner/records.json"
    if os.path.exists(file_path):
        with open(file_path, "r") as file:
            records = json.load(file)
    else:
        records = {}
    return records
