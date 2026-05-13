import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import os

from urllib.parse import urlparse

def get_image_src(url, headers):
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        soup = BeautifulSoup(response.content, 'html.parser')
        image_links = []
        for img in soup.find_all('img'):
            src = img.get('data-src')
            if src:
                image_links.append(urljoin(url, src))
        return image_links
    else:
        print(f"Failed to fetch HTML from {url}")
        return []

def download_image(url, save_dir):
    response = requests.get(url, stream=True)
    if response.status_code == 200:
        filename = os.path.join(save_dir, os.path.basename(urlparse(url).path))
        with open(filename, 'wb') as f:
            for chunk in response.iter_content(1024):
                f.write(chunk)
        print(f"Downloaded {url}")
    else:
        print(f"Failed to download {url}")

# 示例用法
url = 'https://www.bilibili.com/read/cv34795176'  # 输入要获取HTML的网页URL

# 添加自定义请求头
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
}

# 获取网页中所有图片的src链接
image_src_links = get_image_src(url, headers)

# 显示图片的src链接
for link in image_src_links:
    print(link)

# 示例用法
save_dir = 'downloaded_images'  # 输入保存图片的目录

# 创建保存目录
if not os.path.exists(save_dir):
    os.makedirs(save_dir)

# 根据之前获取的图片链接列表进行下载
for link in image_src_links:
    download_image(link, save_dir)