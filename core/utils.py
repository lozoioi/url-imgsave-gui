import hashlib
import os


ALLOWED_EXTENSIONS = {'.gif', '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.svg'}


def get_file_hash(filepath):
    """Calculate MD5 hash of a file."""
    hasher = hashlib.md5()
    try:
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                hasher.update(chunk)
    except OSError:
        return None
    return hasher.hexdigest()


def get_url_hash(url):
    """MD5 hash of a URL string, used for caching/identification."""
    return hashlib.md5(url.encode()).hexdigest()


def filter_by_type(urls, allowed_types):
    """Filter image URLs by file extension.
    allowed_types: set of extensions like {'.gif', '.png', '.jpg'}
    """
    if not allowed_types or 'all' in allowed_types:
        return urls
    return [u for u in urls if os.path.splitext(u.split('?')[0])[1].lower() in allowed_types]


def filter_by_min_size(filepath, min_bytes):
    """Check if a file meets minimum size requirement."""
    try:
        return os.path.getsize(filepath) >= min_bytes
    except OSError:
        return False


def safe_filename(url, default_ext='.jpg'):
    """Generate a safe filename from a URL."""
    from urllib.parse import urlparse, unquote
    path = unquote(urlparse(url).path)
    basename = os.path.basename(path)
    if not basename:
        basename = get_url_hash(url) + default_ext
    basename = basename.split('?')[0]
    return basename


def has_duplicate_by_hash(filepath, known_hashes):
    """Check if a file's MD5 hash already exists in known_hashes set."""
    fhash = get_file_hash(filepath)
    if fhash is None:
        return False
    if fhash in known_hashes:
        return True
    known_hashes.add(fhash)
    return False


def scan_directory_hashes(directory):
    """Scan a directory and return a set of MD5 hashes for all files."""
    hashes = set()
    if not os.path.isdir(directory):
        return hashes
    for fname in os.listdir(directory):
        fpath = os.path.join(directory, fname)
        if os.path.isfile(fpath):
            fhash = get_file_hash(fpath)
            if fhash:
                hashes.add(fhash)
    return hashes
