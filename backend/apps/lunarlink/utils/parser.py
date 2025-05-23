# -*- coding: utf-8 -*-
"""
@File    : parser.py
@Time    : 2023/1/14 15:10
@Author  : geekbing
@LastEditTime : -
@LastEditors : -
@Description : API用例解析
"""
import datetime
import json
import logging

from ast import literal_eval
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Generator, List, Tuple, Union

import json5
import requests

from lunarlink import models
from lunaruser import models as user_models
from lunarlink.utils.tree import (
    get_all_ycatid,
    get_tree_max_id,
    get_tree_ycatid_mapping,
)

logger = logging.getLogger(__name__)


class Format:
    """
    解析标准HttpRunner脚本 前端->后端
    """

    def __init__(self, body: Dict, level: str = "test"):
        """初始化要解析的参数

        body => {
                    header: header -> [{key:'', value:'', desc:''},],
                    request: request -> {
                        form: formData - > [{key: '', value: '', type: 1, desc: ''},],
                        json: jsonData -> {},-
                        params: paramsData -> [{key: '', value: '', type: 1, desc: ''},]
                        files: files -> {"fields","binary"}
                    },
                    extract: extract -> [{key:'', value:'', desc:''}],
                    validate: validate -> [{expect: '', actual: '', comparator: 'equals', type: 1},],
                    variables: variables -> [{key: '', value: '', type: 1, desc: ''},],
                    hooks: hooks -> [{setup: '', teardown: ''},],
                    url: url -> string
                    method: method -> string
                    name: name -> string
                }
        """
        try:
            self.name = body.pop("name", None)
            self.__headers = body.get("header", {}).get("header", {})
            self.__variables = body.get("variables", {}).get("variables", {})
            self.__setup_hooks = body.get("hooks", {}).get("setup_hooks", {})
            self.__teardown_hooks = body.get("hooks", {}).get("teardown_hooks", {})

            if level == "test":
                # 配置移除request参数
                self.__params = (
                    body.get("request", {}).get("params", {}).get("params", {})
                )
                self.__data = body.get("request", {}).get("form", {}).get("data", {})
                self.__json = body.get("request", {}).get("json", {})
                self.__files = body.get("request", {}).get("files", {}).get("files", {})

                self.__desc = {
                    "header": body.get("header", {}).get("desc", {}),
                    "data": body.get("request", {}).get("form", {}).get("desc", {}),
                    "files": body.get("request", {}).get("files", {}).get("desc", {}),
                    "params": body.get("request", {}).get("params", {}).get("desc", {}),
                    "variables": body.get("variables", {}).get("desc", {}),
                }
                self.url = body.pop("url", None)
                self.method = body.pop("method", None)

                self.__times = body.pop("times", None)
                self.__extract = body.get("extract", {}).get("extract", {})
                self.__validate = body.pop("validate", {}).get("validate", {})
                self.__desc["extract"] = body.get("extract", {}).get("desc", {})
            else:
                self.__params = {}
                self.__data = {}
                self.__json = {}
                self.__files = {}

            if level == "config":
                self.__desc = {
                    "header": body.get("header", {}).get("desc", {}),
                    "variables": body.get("variables", {}).get("desc", {}),
                }

                self.base_url = body.pop("base_url")
                self.is_default = body.pop("is_default")
                self.__parameters = body["parameters"].pop("parameters")
                self.__desc["parameters"] = body["parameters"].pop("desc")

            self.__level = level
            self.testcase = None
            self.project = body.pop("project", None)
            self.relation = body.pop("nodeId", None)
            # lunarlink的API没有rig_id字段，需要兼容
            self.rig_id = body.get("rig_id", 0)
            self.rig_env = body.get("rig_env", 0)
        except KeyError:
            pass

    def parse(self):
        """
        返回标准化HttpRunner "desc" 字段, 执行时需去除
        :return:
        """
        if not hasattr(self, "rig_id"):
            self.rig_id = None

        if not hasattr(self, "rig_env"):
            self.rig_env = 0

        test = {}
        if self.__level == "test":
            test = {
                "name": self.name,
                "rig_id": self.rig_id,
                "times": self.__times,
                "request": {"url": self.url, "method": self.method, "verify": False},
                "desc": self.__desc,
            }

            if self.__extract:
                test["extract"] = self.__extract
            if self.__validate:
                test["validate"] = self.__validate

        elif self.__level == "config":
            test = {
                "name": self.name,
                "request": {
                    "base_url": self.base_url,
                },
                "desc": self.__desc,
            }

            if self.__parameters:
                test["parameters"] = self.__parameters

        if self.__headers:
            test["request"]["headers"] = self.__headers
        if self.__params:
            test["request"]["params"] = self.__params
        if self.__data:
            test["request"]["data"] = self.__data
        if self.__json:
            test["request"]["json"] = self.__json
        # 兼容一些接口需要传空json
        if self.__json == {}:
            test["request"]["json"] = {}
        if self.__files:
            test["request"]["files"] = self.__files
        if self.__variables:
            test["variables"] = self.__variables
        if self.__setup_hooks:
            test["setup_hooks"] = self.__setup_hooks
        if self.__teardown_hooks:
            test["teardown_hooks"] = self.__teardown_hooks

        self.testcase = test


class Parse:
    """
    标准HttpRunner脚本解析至前端 后端->前端
    """

    def __init__(self, body: Dict, level: str = "test"):
        """
        body: => {
                "name": "get token with $user_agent, $os_platform, $app_version",
                "request": {
                    "url": "/api/get-token",
                    "method": "POST",
                    "headers": {
                        "app_version": "$app_version",
                        "os_platform": "$os_platform",
                        "user_agent": "$user_agent"
                    },
                    "json": {
                        "sign": "${get_sign($user_agent, $device_sn, $os_platform, $app_version)}"
                    },
                    "extract": [
                        {"token": "content.token"}
                    ],
                    "validate": [
                        {"eq": ["status_code", 200]},
                        {"eq": ["headers.Content-Type", "application/json"]},
                        {"eq": ["content.success", true]}
                    ],
                    "setup_hooks": [],
                    "teardown_hooks": []
                }
        """
        self.name = body.get("name")
        self.__request = body.get("request")  #
        self.__variables = body.get("variables")
        self.__setup_hooks = body.get("setup_hooks", [])
        self.__teardown_hooks = body.get("teardown_hooks", [])
        self.__desc = body.get("desc")

        if level == "test":
            self.__times = body.get("times", 1)  # 如果导入没有times 默认为1
            self.__extract = body.get("extract")
            self.__validate = body.get("validate")

        if level == "config":
            self.__parameters = body.get("parameters")

        self.__level = level
        self.testcase = None

    @staticmethod
    def __get_type(content: Any) -> Tuple:
        """返回data_type 默认string"""
        var_type = {
            "str": 1,
            "int": 2,
            "float": 3,
            "bool": 4,
            "list": 5,
            "dict": 6,
            "NoneType": 7,
        }

        key = str(type(content).__name__)

        # 黑魔法，为了兼容值是int，但又是$引用变量的情况
        if key == "str" and "$int" in content:
            return var_type["int"], content

        if key == "NoneType":
            return var_type["NoneType"], content

        if key in ["list", "dict"]:
            content = json.dumps(content, ensure_ascii=False)
        else:
            content = str(content)

        return var_type[key], content

    def parse_http(self):
        """解析成标准前端脚本格式"""

        init = [
            {
                "key": "",
                "value": "",
                "desc": "",
            }
        ]

        init_p = [
            {
                "key": "",
                "value": "",
                "desc": "",
                "type": 1,
            }
        ]

        # 初始化test结构
        test = {
            "name": self.name,
            "header": init,
            "request": {
                "data": init_p,
                "params": init_p,
                "json_data": "",
            },
            "variables": init_p,
            "hooks": [
                {
                    "setup": "",
                    "teardown": "",
                }
            ],
        }

        if self.__level == "test":
            test["times"] = self.__times
            test["method"] = self.__request["method"]
            test["url"] = self.__request["url"]
            test["validate"] = [
                {
                    "expect": "",
                    "actual": "",
                    "comparator": "equals",
                    "type": 1,
                }
            ]
            test["extract"] = init

            if self.__extract:
                test["extract"] = []
                for content in self.__extract:
                    for key, value in content.items():
                        test["extract"].append(
                            {
                                "key": key,
                                "value": value,
                                "desc": self.__desc["extract"][key],
                            }
                        )

            if self.__validate:
                test["validate"] = []
                for content in self.__validate:
                    for key, value in content.items():
                        obj = Parse.__get_type(value[1])
                        # 兼容旧的断言
                        desc = ""
                        if len(value) >= 3:
                            # value[2]为None时，设置为''
                            desc = value[2] or ""

                        test["validate"].append(
                            {
                                "expect": obj[1],
                                "actual": value[0],
                                "comparator": key,
                                "type": obj[0],
                                "desc": desc,
                            }
                        )
        elif self.__level == "config":
            test["base_url"] = self.__request["base_url"]
            test["parameters"] = init

            if self.__parameters:
                test["parameters"] = []
                for content in self.__parameters:
                    for key, value in content.items():
                        test["parameters"].append(
                            {
                                "key": key,
                                "value": Parse.__get_type(value)[1],
                                "desc": self.__desc["parameters"][key],
                            }
                        )

        if self.__request.get("headers"):
            test["header"] = []
            for key, value in self.__request.pop("headers").items():
                test["header"].append(
                    {"key": key, "value": value, "desc": self.__desc["header"][key]}
                )

        if self.__request.get("data"):
            test["request"]["data"] = []
            for key, value in self.__request.pop("data").items():
                obj = Parse.__get_type(value)
                test["request"]["data"].append(
                    {
                        "key": key,
                        "value": obj[1],
                        "type": obj[0],
                        "desc": self.__desc["data"][key],
                    }
                )

        if self.__request.get("params"):
            test["request"]["params"] = []
            for key, value in self.__request.pop("params").items():
                test["request"]["params"].append(
                    {
                        "key": key,
                        "value": value,
                        "type": 1,
                        "desc": self.__desc["params"][key],
                    }
                )

        if self.__request.get("json"):
            test["request"]["json_data"] = json.dumps(
                self.__request.pop("json"),
                indent=4,
                separators=(",", ": "),
                ensure_ascii=False,
            )

        if self.__variables:
            test["variables"] = []
            for content in self.__variables:
                for key, value in content.items():
                    obj = Parse.__get_type(value)
                    test["variables"].append(
                        {
                            "key": key,
                            "value": obj[1],
                            "desc": self.__desc["variables"][key],
                            "type": obj[0],
                        }
                    )

        if self.__setup_hooks or self.__teardown_hooks:
            test["hooks"] = []
            if len(self.__setup_hooks) > len(self.__teardown_hooks):
                for index in range(0, len(self.__setup_hooks)):
                    teardown = ""
                    if index < len(self.__teardown_hooks):
                        teardown = self.__teardown_hooks[index]
                    test["hooks"].append(
                        {
                            "setup": self.__setup_hooks[index],
                            "teardown": teardown,
                        }
                    )
            else:
                for index in range(0, len(self.__teardown_hooks)):
                    setup = ""
                    if index < len(self.__setup_hooks):
                        setup = self.__setup_hooks[index]
                    test["hooks"].append(
                        {
                            "setup": setup,
                            "teardown": self.__teardown_hooks[index],
                        }
                    )

        self.testcase = test


class Yapi:
    def __init__(
        self,
        yapi_base_url: str,
        token: str,
        faster_project_id: int,
    ):
        self.__yapi_base_url = yapi_base_url
        self.__token = token
        self.faster_project_id = faster_project_id
        self.api_info: List = []
        self.api_ids: List = []
        # self.category_info: List = []
        # api基础信息，不包含请求报文
        self.api_list_url = self.__yapi_base_url + "/api/interface/list"
        # api详情，包含详细的请求报文
        self.api_details_url = self.__yapi_base_url + "/api/interface/get"
        # api所有分组目录，也包含了api的基础信息
        self.category_info_url = self.__yapi_base_url + "/api/interface/list_menu"

    def get_category_info(self) -> Dict:
        """获取接口菜单列表
        :return:
        """
        try:
            res = requests.get(
                self.category_info_url, params={"token": self.__token}
            ).json()
        except Exception as e:
            logger.error(f"获取yapi的目录失败：{e}")
        else:
            if res["errcode"] == 0:
                return res
            else:
                return {"errcode": 1, "errmsg": "获取yapi的目录失败！", "data": []}

    def get_api_uptime_mapping(self):
        """yapi所有api的更新时间映射关系，{api_id: api_up_time}
        :return:
        """
        ""
        category_info_list = self.get_category_info()
        mapping = {}
        for category_info in category_info_list["data"]:
            category_detail = category_info.get("list", [])
            for category in category_detail:
                api_id = category["_id"]
                up_time = category["up_time"]
                mapping[api_id] = up_time
        return mapping

    def get_category_id_name_mapping(self):
        """获取yapi的分组信息映射关系，{category_id: category_name}
        :return:
        """

        try:
            res = self.get_category_info()
        except Exception as e:
            logger.error(f"获取yapi的目录失败：{e}")
        else:
            if res["errcode"] == 0:
                # {'category_id': 'category_name'}
                category_id_name_mapping = {}
                for category_info in res["data"]:
                    # 排除为空的分组
                    if category_info.get("list"):
                        category_name = category_info.get("name")
                        category_id = category_info.get("_id")
                        category_id_name_mapping[category_id] = category_name
                return category_id_name_mapping

    def get_api_info_list(self):
        """获取接口列表数据
        :return:
        """
        try:
            res = requests.get(
                self.api_list_url,
                params={
                    "token": self.__token,
                    "page": 1,
                    "limit": 100000,
                },
            ).json()
        except Exception as e:
            logger.error(f"获取api list失败: {e}")
        else:
            if res["errcode"] == 0:
                return res

    def get_api_ids(self) -> List:
        """
        获取yapi的api_ids
        :return:
        """
        api_list = self.get_api_info_list()
        return [api["id"] for api in api_list["data"]["list"]]

    def get_batch_api_detail(self, api_ids: List[int]) -> Generator[dict, None, None]:
        """
        获取yapi的所有api的详细信息
        :param api_ids:
        :return:
        """
        token = self.__token
        session = requests.Session()  # 创建一个 Session 对象

        def fetch_api_detail(api_id):
            try:
                response = session.get(
                    f"{self.api_details_url}?token={token}&id={api_id}"
                )
                response.raise_for_status()  # 如果状态码不是200，会引发HTTPError异常
                res = response.json()
                return res["data"]
            except requests.HTTPError as http_err:
                logger.error(f"HTTP error occurred: {http_err}")
            except Exception as e:
                logger.error(f"Error occurred: {e}")

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(fetch_api_detail, api_id) for api_id in api_ids}
            for future in as_completed(futures):
                api_detail = future.result()
                if api_detail is not None:
                    yield api_detail  # 使用 yield 关键字，返回一个生成器

    @staticmethod
    def get_variable_default_value(
        variable_type: str, variable_value: Union[Dict, Any]
    ):
        """
        获取变量默认值
        :param variable_type:
        :param variable_value:
        :return:
        """

        if isinstance(variable_value, dict) is False:
            return ""
        variable_type = variable_type.lower()
        if variable_type in ("integer", "number", "bigdecimal"):
            return variable_value.get("default", 0)
        elif variable_type == "date":
            return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        elif variable_type == "string":
            return ""

        return ""

    def create_relation_id(self, project_id):
        """创建yapi所属目录
        :param project_id:
        :return:
        """
        category_id_name_mapping: Dict = self.get_category_id_name_mapping()
        obj = models.Relation.objects.get(project_id=project_id, type=1)
        eval_tree: List = literal_eval(obj.tree)
        yapi_catids: List = [yapi_catid for yapi_catid in get_all_ycatid(eval_tree, [])]

        if category_id_name_mapping is None:
            return
        for cat_id, cat_name in category_id_name_mapping.items():
            if cat_id not in yapi_catids:
                tree_id = get_tree_max_id(tree=eval_tree)
                base_tree_node = {
                    "id": tree_id + 1,
                    "yapi_catid": cat_id,
                    "label": cat_name,
                    "children": [],
                }
                eval_tree.append(base_tree_node)

        obj.tree = json.dumps(eval_tree, ensure_ascii=False)
        obj.save()

    def yapi2faster(self, source_api_info):
        """yapi单个api转成faster格式
        :param source_api_info:
        :return:
        """

        logger.info(f"正在处理yapi的接口id是{source_api_info.get('_id')}")
        api_info_template = {
            "header": {
                "header": {},
                "desc": {},
            },
            "request": {
                "form": {
                    "data": {},
                    "desc": {},
                },
                "json": {},
                "params": {
                    "params": {},
                    "desc": {},
                },
                "files": {
                    "files": {},
                    "desc": {},
                },
            },
            "extract": {
                "extract": [],
                "desc": {},
            },
            "validate": {
                "validate": [],
            },
            "variables": {
                "variables": [],
                "desc": {},
            },
            "hooks": {
                "setup_hooks": [],
                "teardown_hooks": [],
            },
            "url": "",
            "method": "",
            "name": "",
            "times": 1,
            "nodeId": 0,
            "project": self.faster_project_id,
        }

        default_validator = {"equals": ["status_code", 200]}
        api_info_template["validate"]["validate"].append(default_validator)

        # 限制api的名称最大长度，避免溢出
        api_info_template["name"] = source_api_info.get("title", "默认api名称")[:100]

        # path中{var}替换成$var格式
        api_info_template["url"] = (
            source_api_info.get("path", "").replace("{", "$").replace("}", "")
        )
        api_info_template["method"] = source_api_info.get("method", "GET")

        # yapi的分组id
        api_info_template["yapi_catid"] = source_api_info["catid"]
        api_info_template["yapi_id"] = source_api_info["_id"]

        # 十位时间戳
        api_info_template["yapi_add_time"] = source_api_info.get("add_time", "")
        api_info_template["yapi_up_time"] = source_api_info.get("up_time", "")

        # yapi原作者名
        api_info_template["yapi_username"] = source_api_info.get("username", "")

        req_body_type = source_api_info.get("req_body_type")
        req_body_other = source_api_info.get("req_body_other", "")
        if req_body_type == "json" and req_body_other != "":
            try:
                req_body = json.loads(req_body_other)
            except json.decoder.JSONDecodeError:
                # TODO: 解析带注释的json req_body没起作用
                req_body = json5.loads(req_body_other, encoding="utf8")
            except Exception as e:
                logger.error(
                    f"yapi: {source_api_info['_id']}, req_body json loads failed: {source_api_info.get('req_body_other', e)}"
                )
            else:
                # TODO: 递归遍历properties所有节点
                if isinstance(req_body, dict):
                    req_body_properties = req_body.get("properties")
                    if isinstance(req_body_properties, dict):
                        for field_name, field_value in req_body_properties.items():
                            if isinstance(field_value, dict) is False:
                                continue
                            any_of = field_value.get("anyOf")
                            if isinstance(any_of, list):
                                if len(any_of) > 0:
                                    field_value: dict = any_of[0]

                            field_type = field_value.get("type", "unKnow")
                            if field_type == "unKnow":
                                logger.error(
                                    f'yapi: {source_api_info["_id"]}, req_body json type is unKnow'
                                )

                            if not (field_type == "array" or field_type == "object"):
                                self.set_ordinary_variable(
                                    api_info_template=api_info_template,
                                    field_name=field_name,
                                    field_type=field_type,
                                    field_value=field_value,
                                )

                            if field_type == "array":
                                items: dict = field_value["items"]

                                # 特殊字段处理，通用的查询条件
                                if field_name == "conditions":
                                    set_customized_variable(api_info_template, items)
                                else:
                                    items_type: str = items.get("type")
                                    if items_type != "array" and items_type != "object":
                                        self.set_ordinary_variable(
                                            api_info_template=api_info_template,
                                            field_name=field_name,
                                            field_type=field_type,
                                            field_value=field_value,
                                        )

                            if field_type == "object":
                                properties: dict = field_value.get("properties")
                                if properties and isinstance(properties, dict):
                                    for (
                                        property_name,
                                        property_value,
                                    ) in properties.items():
                                        field_type = property_value["type"]
                                        if not (
                                            field_type == "array"
                                            or field_type == "object"
                                        ):
                                            self.set_ordinary_variable(
                                                api_info_template=api_info_template,
                                                field_name=property_name,
                                                field_type=field_type,
                                                field_value=property_value,
                                            )

        req_query: List = source_api_info.get("req_query", [])
        if req_query:
            for param in req_query:
                param_name = param["name"]
                param_desc = param.get("desc", "")
                param_example = param.get("example", "")
                api_info_template["request"]["params"]["params"][
                    param_name
                ] = f"${param_name}"
                api_info_template["request"]["params"]["desc"][param_name] = param_desc
                api_info_template["variables"]["variables"].append(
                    {param_name: param_example}
                )
                api_info_template["variables"]["desc"][param_name] = param_desc

        req_body_form: List = source_api_info.get("req_body_form", [])
        if req_body_form:
            for data in req_body_form:
                form_name = data.get("name")
                form_desc = data.get("desc", "")
                form_example = data.get("example", "")
                api_info_template["request"]["form"]["data"][
                    form_name
                ] = f"${form_name}"
                api_info_template["request"]["form"]["desc"][form_name] = form_desc
                api_info_template["variables"]["variables"].append(
                    {form_name: form_example}
                )
                api_info_template["variables"]["desc"][form_name] = form_desc

        req_params: List = source_api_info.get("req_params", [])
        if req_params:
            for param in req_params:
                param_name = param.get("name")
                param_desc = param.get("desc", "")
                param_example = param.get("example", "")
                api_info_template["variables"]["variables"].append(
                    {param_name: param_example}
                )
                api_info_template["variables"]["desc"][param_name] = param_desc

        return api_info_template

    def set_ordinary_variable(
        self, api_info_template, field_name, field_type, field_value
    ):
        api_info_template["request"]["json"][field_name] = f"${field_name}"
        api_info_template["variables"]["variables"].append(
            {field_name: self.get_variable_default_value(field_type, field_value)}
        )
        api_info_template["variables"]["desc"][field_name] = field_value.get(
            "description", ""
        )

    def get_parsed_apis(self, api_info) -> List:
        """
        批量创建fastapi格式的api
        :param api_info:
        :return: 返回多个 API 类的实例
        """

        apis = [
            self.yapi2faster(api) for api in api_info if isinstance(api, dict) is True
        ]
        proj = models.Project.objects.get(id=self.faster_project_id)
        obj = models.Relation.objects.get(project_id=self.faster_project_id, type=1)
        yapi_user = user_models.MyUser.objects.filter(name="yapi").first()
        yapi_user_id = yapi_user.id if yapi_user else None
        eval_tree: List = literal_eval(obj.tree)
        tree_ycatid_mapping = get_tree_ycatid_mapping(value=eval_tree)
        api_instances = []
        for api in apis:
            format_api = Format(api)
            format_api.parse()
            yapi_catid: int = api["yapi_catid"]
            api_body = {
                "name": format_api.name,
                "body": format_api.testcase,
                "url": format_api.url,
                "method": format_api.method,
                "project": proj,
                "relation": tree_ycatid_mapping.get(yapi_catid, 0),
                # 直接从yapi原来的api中获取
                "yapi_catid": yapi_catid,
                "yapi_id": api["yapi_id"],
                "yapi_add_time": api["yapi_add_time"],
                "yapi_up_time": api["yapi_up_time"],
                "yapi_username": api["yapi_username"],
                # 默认为yapi用户
                "creator_id": yapi_user_id,
            }
            api_instances.append(models.API(**api_body))

        return api_instances

    @staticmethod
    def merge_api(
        api_instances: List, apis_imported_from_yapi: List
    ) -> Tuple[List, List]:
        """
        将 yapi 获取的 api 和已导入测试平台的 api 进行合并
        两种情况：
        1. parsed_api.yapi_id不存在测试平台
        2. yapi的id已经存在测试平台，新获取的 parsed_api.yapi_up_time > imported_api.yapi_up_time
        :param api_instances: 解析后的 API 实例
        :param apis_imported_from_yapi: 原 api 信息
        :return: 返回要更新的 API 实例和要新增的 API 实例
        """
        imported_apis_mapping = {
            api.yapi_id: api.yapi_up_time for api in apis_imported_from_yapi
        }
        imported_apis_index = {
            api.yapi_id: index for index, api in enumerate(apis_imported_from_yapi)
        }

        new_api_instances = []
        update_api_instances = []
        imported_apis_ids = set(imported_apis_mapping.keys())
        for api in api_instances:
            yapi_id = api.yapi_id
            # parsed_api.yapi_id不存在测试平台
            if yapi_id not in imported_apis_ids:
                new_api_instances.append(api)
            else:
                # yapi的id已经存在测试平台
                imported_yapi_up_time = imported_apis_mapping[yapi_id]
                if api.yapi_up_time > int(imported_yapi_up_time):
                    index = imported_apis_index[yapi_id]
                    imported_api = apis_imported_from_yapi[index]
                    imported_api.method = api.method
                    imported_api.name = api.name
                    imported_api.url = api.url
                    imported_api.body = api.body
                    imported_api.yapi_up_time = api.yapi_up_time

                    update_api_instances.append(imported_api)

        return update_api_instances, new_api_instances

    def get_create_or_update_apis(self, imported_apis_mapping):
        """
        返回需要新增和更新的api_id
        imported_apis_mapping: {yapi_id: yapi_up_time}
        新增：
            yapi_id不存在测试平台imported_apis_mapping中
        更新：
            yapi_id存在测试平台imported_apis_mapping, 且up_time大于测试平台的
        :param imported_apis_mapping:
        :return:
        """
        api_uptime_mapping: Dict = self.get_api_uptime_mapping()

        create_ids = []
        update_ids = []
        for yapi_id, yapi_up_time in api_uptime_mapping.items():
            imported_yapi_up_time = imported_apis_mapping.get(yapi_id)
            if not imported_yapi_up_time:
                # 新增
                create_ids.append(yapi_id)
            elif yapi_up_time > int(imported_yapi_up_time):
                # 更新
                update_ids.append(yapi_id)

        return create_ids, update_ids


# 特殊字段conditions
def set_customized_variable(api_info_template, items):
    if items["type"] == "object":
        properties: dict = items["properties"]
        attr_name: dict = properties.get("attributeName", {})
        attribute_name_enum: list = attr_name.get("enum", [""])
        if len(attribute_name_enum) == 0:
            attribute_name_enum = [""]
        target_value: list = [f"${value}" for value in attribute_name_enum]
        # 查询条件字段默认模板
        api_info_template["request"]["json"]["conditions"] = {
            "attributeName": f"${attribute_name_enum[0]}",
            "rangeType": "$rangeType",
            "targetValue": target_value,
        }
        for attr in attribute_name_enum:
            api_info_template["variables"]["variables"].append({attr: ""})
            api_info_template["variables"]["desc"][attr] = attr_name.get(
                "description", ""
            )

        # 查询条件比较类型
        range_type: dict = properties.get("rangeType", {})
        range_type_enum: list = range_type.get("enum", [""])
        api_info_template["variables"]["variables"].append(
            {"rangeType": range_type_enum[0]}
        )
        api_info_template["variables"]["desc"][
            "rangeType"
        ] = f"条件匹配方式：{','.join(range_type_enum)}"

        # 默认排序
        api_info_template["request"]["json"]["orderBy"] = [
            {
                "attributeName": f"${attribute_name_enum[0]}",
                "rankType": "DESC",
            }
        ]


def format_json(value):
    """
    将一个JSON对象格式化为易读的字符串。

    如果输入值因为任何原因无法被序列化为JSON，就返回原始输入值。

    :param value: 需要格式化的JSON对象
    :return: 如果成功，返回格式化后的字符串；否则，返回原始输入值。
    """
    try:
        return json.dumps(value, indent=4, separators=(",", ": "), ensure_ascii=False)
    except (TypeError, OverflowError):
        return value
