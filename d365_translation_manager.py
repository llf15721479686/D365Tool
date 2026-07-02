"""翻译管理器 —— 支持百度翻译 API 和 有道智云 API。"""

from __future__ import annotations

import hashlib
import json
import random
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# D365 语言 LCID 编码
# ============================================================
LANGUAGE_LCID: Dict[str, str] = {
    "英语": "1033",
    "阿拉伯语": "1025",
    "德语": "1031",
    "法语": "1036",
    "意大利语": "1040",
    "葡萄牙语": "1046",
    "俄语": "1049",
    "泰语": "1054",
    "印度尼西亚语": "1057",
    "越南语": "1066",
    "马来西亚语": "1086",
    "汉语": "2052",
    "西班牙语": "3082",
}


def get_lang_label(lang_name: str) -> str:
    """返回带 LCID 编码的语言标签，如 '英语 [1033]'。"""
    lcid = LANGUAGE_LCID.get(lang_name, "")
    if lcid:
        return f"{lang_name} [{lcid}]"
    return lang_name
# ============================================================

# 百度翻译语言代码
BAIDU_LANG_CODES: Dict[str, str] = {
    "英语": "en",
    "阿拉伯语": "ara",
    "德语": "de",
    "法语": "fra",
    "意大利语": "ita",
    "葡萄牙语": "pt",
    "俄语": "ru",
    "泰语": "th",
    "印度尼西亚语": "id",
    "越南语": "vie",
    "马来西亚语": "ms",
    "汉语": "zh",
    "西班牙语": "spa",
}

# 有道智云语言代码
YOUDAO_LANG_CODES: Dict[str, str] = {
    "英语": "en",
    "阿拉伯语": "ar",
    "德语": "de",
    "法语": "fr",
    "意大利语": "it",
    "葡萄牙语": "pt",
    "俄语": "ru",
    "泰语": "th",
    "印度尼西亚语": "id",
    "越南语": "vi",
    "马来西亚语": "ms",
    "汉语": "zh-CHS",
    "西班牙语": "es",
}

# 需要使用有道 API 的语言（目前为印尼语、马来语、意大利语）
YOUDAO_LANGUAGES = {"印度尼西亚语", "马来西亚语", "意大利语"}


def _get_sign_baidu(app_id: str, q: str, salt: str, secret_key: str) -> str:
    sign_str = app_id + q + salt + secret_key
    return hashlib.md5(sign_str.encode("utf-8")).hexdigest()


def _get_sign_youdao(app_key: str, q: str, salt: str, curtime: str, app_secret: str) -> str:
    sign_str = app_key + q + salt + curtime + app_secret
    return hashlib.sha256(sign_str.encode("utf-8")).hexdigest()


def translate_baidu(
    text: str,
    target_lang: str,
    app_id: str,
    secret_key: str,
    api_url: str = "",
) -> str:
    """调用百度翻译 API 进行翻译。"""
    if not app_id or not secret_key or not api_url:
        raise RuntimeError("百度翻译 API 未配置，请在 config.json 中设置 baidu_app_id、baidu_secret_key、baidu_api_url。")
    salt = str(random.randint(32768, 65536))
    sign = _get_sign_baidu(app_id, text, salt, secret_key)
    params = {
        "q": text,
        "from": "auto",
        "to": target_lang,
        "appid": app_id,
        "salt": salt,
        "sign": sign,
    }
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(api_url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"百度翻译请求失败: {exc}") from exc

    if "error_code" in result:
        error_msg = result.get("error_msg", result.get("error_code", "未知错误"))
        raise RuntimeError(f"百度翻译错误 ({result['error_code']}): {error_msg}")

    trans_result = result.get("trans_result", [])
    if trans_result:
        return trans_result[0].get("dst", "")
    return ""


def translate_youdao(
    text: str,
    target_lang: str,
    app_key: str,
    app_secret: str,
    api_url: str = "",
) -> str:
    """调用有道智云翻译 API 进行翻译。"""
    if not app_key or not app_secret or not api_url:
        raise RuntimeError("有道翻译 API 未配置，请在 config.json 中设置 youdao_app_key、youdao_app_secret、youdao_api_url。")
    salt = str(random.randint(32768, 65536))
    curtime = str(int(time.time()))
    sign = _get_sign_youdao(app_key, text, salt, curtime, app_secret)
    params = {
        "q": text,
        "from": "auto",
        "to": target_lang,
        "appKey": app_key,
        "salt": salt,
        "sign": sign,
        "signType": "v3",
        "curtime": curtime,
    }
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(api_url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"有道翻译请求失败: {exc}") from exc

    error_code = result.get("errorCode", "0")
    if error_code != "0":
        error_msg = {
            "101": "缺少必填参数",
            "102": "不支持的语言类型",
            "103": "翻译文本过长",
            "104": "不支持的API类型",
            "105": "不支持的签名类型",
            "106": "响应格式无效",
            "107": "翻译接口已关闭",
            "108": "应用ID无效",
            "109": "batchLog参数格式不正确",
            "110": "无相关服务",
            "111": "开发者账号无效",
            "113": "查询为空",
            "201": "解密失败，签名错误",
            "202": "签名校验失败",
            "203": "访问IP地址不在可访问IP列表",
            "301": "辞典查询失败",
            "302": "翻译查询失败",
            "303": "服务端的其它异常",
            "401": "账户已经欠费",
            "411": "请求频率过快",
            "412": "请求超过最大字符",
        }.get(error_code, f"未知错误 (code={error_code})")
        raise RuntimeError(f"有道翻译错误: {error_msg}")

    translation = result.get("translation", [])
    if translation:
        return translation[0]
    return ""


class D365TranslationManager:
    """实体翻译管理器。"""

    # 可选的语言列表（中文名）
    AVAILABLE_LANGUAGES: List[str] = [
        "英语[1033]",
        "阿拉伯语[1025]",
        "德语[1031]",
        "法语[1036]",
        "意大利语[1040]",
        "葡萄牙语[1046]",
        "俄语[1049]",
        "泰语[1054]",
        "印度尼西亚语[1057]",
        "越南语[1066]",
        "马来西亚语[1086]",
        "汉语[2052]",
        "西班牙语[3082]",
    ]

    def __init__(
        self,
        baidu_app_id: str = "",
        baidu_secret_key: str = "",
        baidu_api_url: str = "",
        youdao_app_key: str = "",
        youdao_app_secret: str = "",
        youdao_api_url: str = "",
    ) -> None:
        self.baidu_app_id = baidu_app_id
        self.baidu_secret_key = baidu_secret_key
        self.baidu_api_url = baidu_api_url
        self.youdao_app_key = youdao_app_key
        self.youdao_app_secret = youdao_app_secret
        self.youdao_api_url = youdao_api_url

    def translate(self, text: str, lang_name: str) -> str:
        """翻译单个文本到指定语言。

        Args:
            text: 要翻译的文本。
            lang_name: 语言中文名（如 "英语", "德语"）。

        Returns:
            翻译后的文本。
        """
        if not text.strip():
            return ""

        if lang_name in YOUDAO_LANGUAGES:
            lang_code = YOUDAO_LANG_CODES.get(lang_name, "en")
            return translate_youdao(
                text,
                lang_code,
                self.youdao_app_key,
                self.youdao_app_secret,
                self.youdao_api_url,
            )
        else:
            lang_code = BAIDU_LANG_CODES.get(lang_name, "en")
            return translate_baidu(
                text,
                lang_code,
                self.baidu_app_id,
                self.baidu_secret_key,
                self.baidu_api_url,
            )

    def batch_translate(
        self, texts: List[str], lang_names: List[str]
    ) -> Dict[str, Dict[str, str]]:
        """批量翻译多个文本到多个目标语言。

        Args:
            texts: 源文本列表。
            lang_names: 目标语言中文名列表。

        Returns:
            {源文本: {语言名: 翻译结果}} 的字典。
        """
        results: Dict[str, Dict[str, str]] = {}
        for text in texts:
            if not text.strip():
                continue
            results[text] = {}
            for lang in lang_names:
                try:
                    results[text][lang] = self.translate(text, lang)
                except Exception as exc:
                    results[text][lang] = f"[错误] {exc}"
        return results

    @staticmethod
    def read_translation_api_config(config_path: str) -> Dict[str, str]:
        """从 config.json 读取翻译 API 配置。"""
        from d365_field_creator import load_config

        cfg = load_config(config_path)
        return {
            "baidu_app_id": str(cfg.get("baidu_app_id") or cfg.get("BaiduAppId") or "").strip(),
            "baidu_secret_key": str(cfg.get("baidu_secret_key") or cfg.get("BaiduSecretKey") or "").strip(),
            "baidu_api_url": str(cfg.get("baidu_api_url") or cfg.get("BaiduApiUrl") or "").strip(),
            "youdao_app_key": str(cfg.get("youdao_app_key") or cfg.get("YoudaoAppKey") or "").strip(),
            "youdao_app_secret": str(cfg.get("youdao_app_secret") or cfg.get("YoudaoAppSecret") or "").strip(),
            "youdao_api_url": str(cfg.get("youdao_api_url") or cfg.get("YoudaoApiUrl") or "").strip(),
        }
