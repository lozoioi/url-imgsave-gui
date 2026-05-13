import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

# 内置平台适配器注册表
_PLATFORM_PARSERS = {}
# B站 API 结果缓存（避免限流：get_image_urls 和 get_page_title 各调一次 API）
_API_CACHE = {}


def _cached_platform_call(url, headers):
    """缓存平台 API 调用，过期时间 60 秒。"""
    import time as _time
    key = url + str(sorted(headers.items()))
    if key in _API_CACHE:
        imgs, title, ts = _API_CACHE[key]
        if _time.time() - ts < 60:
            return imgs, title
    return None


def _cache_platform_result(url, headers, images, title):
    import time as _time
    key = url + str(sorted(headers.items()))
    _API_CACHE[key] = (images, title, _time.time())


def register_platform(domain_pattern, parser_fn):
    """注册平台专用的图片提取器。parser_fn(url, headers) -> list[image_urls]"""
    _PLATFORM_PARSERS[domain_pattern] = parser_fn


def _get_platform_images(url, headers):
    """尝试用注册的平台适配器提取图片。返回 (images, title) 或 (None, None)。"""
    # 先查缓存
    cached = _cached_platform_call(url, headers)
    if cached:
        return cached

    host = urlparse(url).netloc.lower()
    for pattern, parser in _PLATFORM_PARSERS.items():
        if re.search(pattern, host):
            images, title = parser(url, headers)
            _cache_platform_result(url, headers, images, title)
            return images, title
    return None, None


# ============================================================
#  通用 HTML 提取
# ============================================================

def _html_get_image_urls(url, headers, source_attrs):
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"获取页面失败: {url} — {e}")

    soup = BeautifulSoup(response.content, 'html.parser')
    image_urls = []

    for img in soup.find_all('img'):
        for attr in source_attrs:
            val = img.get(attr)
            if val:
                image_urls.append(urljoin(url, val))
                break

    # Also look for <a> tags linking to images
    for a in soup.find_all('a', href=True):
        href = a['href']
        if re.search(r'\.(?:jpg|jpeg|png|gif|webp)(?:\?|$)', href, re.I):
            image_urls.append(urljoin(url, href))

    return image_urls


def _html_get_page_title(url, headers):
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        title = soup.title.string if soup.title else urlparse(url).netloc
        title = re.sub(r'[\\/:*?"<>|]', '_', title.strip())
        return title[:80]
    except Exception:
        return urlparse(url).netloc


# ============================================================
#  B站适配器
# ============================================================

def _bilibili_parser(url, headers):
    """B站专栏文章 API 适配器"""
    # Extract cv ID: cv34795176 -> 34795176
    m = re.search(r'cv(\d+)', url)
    if not m:
        return None, None
    cv_id = m.group(1)

    api_url = f'https://api.bilibili.com/x/article/view?id={cv_id}'
    api_headers = {**headers, 'Referer': 'https://www.bilibili.com/'}

    try:
        resp = requests.get(api_url, headers=api_headers, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"B站API请求失败: {e}")

    data = resp.json()
    if data.get('code') != 0:
        raise RuntimeError(f"B站API返回错误: {data.get('message', data)}")

    article = data['data']
    image_urls = []

    # origin_image_urls 包含原图链接
    image_urls.extend(article.get('origin_image_urls', []))
    image_urls.extend(article.get('image_urls', []))

    # content 中也可能有图片
    content = article.get('content', '') or ''
    for m in re.finditer(r'(?:src|data-src)=["\']([^"\']+)["\']', content):
        url_ = m.group(1)
        if url_.startswith('http'):
            image_urls.append(url_)

    # 去重保序
    seen = set()
    unique = []
    for u in image_urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)

    title = article.get('title', f'bilibili_cv{cv_id}')
    title = re.sub(r'[\\/:*?"<>|]', '_', title.strip()) if title else f'bilibili_cv{cv_id}'
    title = title[:80]

    return unique, title


register_platform(r'bilibili\.com', _bilibili_parser)


# ============================================================
#  公共接口
# ============================================================

def get_image_urls(url, headers, source_attrs=None):
    """
    提取网页中所有图片 URL。
    自动检测已知平台（如B站），使用专用 API 提取；
    否则回退到 HTML 解析。
    """
    if source_attrs is None:
        source_attrs = ['data-src', 'src', 'data-original']

    # 先尝试平台适配器
    images, _ = _get_platform_images(url, headers)
    if images:
        return images

    # 回退：HTML 解析
    return _html_get_image_urls(url, headers, source_attrs)


def get_page_title(url, headers):
    """提取页面标题（优先用平台适配器）。"""
    images, title = _get_platform_images(url, headers)
    if title:
        return title
    return _html_get_page_title(url, headers)
