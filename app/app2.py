import streamlit as st
import os
import glob
import zipfile
import sys
import threading
import hashlib
import hmac
import base64
import boto3
import requests
from botocore.exceptions import ClientError

ENV = "dev"

# AWS clients - Initialize first
SSM_CLIENT = boto3.client('ssm')
S3_CLIENT = boto3.client('s3')

# Configuration
BASE_DIR = '/Media/NAS/Clients/'
BE_API = 'https://api.n3rd-media.com/v1'
CLIENTS_API = f"{BE_API}/clients"
ALBUMS_API = f"{BE_API}/albums"


def get_secret_from_ssm(parameter_name, with_decryption=True):
    """
    Retrieve a secret value from AWS Systems Manager Parameter Store.

    Args:
        parameter_name (str): The name of the parameter you want to retrieve.
        with_decryption (bool): Whether to decrypt the parameter value (for SecureString types).

    Returns:
        str: The value of the parameter if successful, None otherwise.
    """
    try:
        # Get the parameter
        response = SSM_CLIENT.get_parameter(
            Name=parameter_name,
            WithDecryption=with_decryption
        )
        # Return the parameter value
        return response['Parameter']['Value']
    except ClientError as e:
        st.error(f"Failed to retrieve parameter {parameter_name}: {e}")
        return None

# Retrieve HMAC secret key from SSM (cached)
@st.cache_data(ttl=3600)  # Cache for 1 hour
def get_hmac_key():
    """
    Retrieve HMAC secret key from SSM Parameter Store with caching.

    Returns:
        str: HMAC secret key
    """
    return get_secret_from_ssm(f'/album-manager/{ENV}/hmac_key')

def generate_hmac_signature(secret_key, message):
    """
    Generate HMAC SHA256 signature for request authentication.

    Args:
        secret_key (str): Secret key for HMAC
        message (str): Message to sign

    Returns:
        str: Base64 encoded HMAC signature
    """
    if not secret_key or not message:
        raise ValueError("secret_key and message are required")

    message_bytes = message.encode('utf-8') if isinstance(message, str) else message
    secret_key_bytes = secret_key.encode('utf-8') if isinstance(secret_key, str) else secret_key
    signature = hmac.new(secret_key_bytes, message_bytes, hashlib.sha256).digest()
    signature_base64 = base64.b64encode(signature).decode('utf-8')
    return signature_base64

def send_signed_request(url, data=None, headers=None, request_type='get'):
    """
    Send signed HTTP request with HMAC authentication.

    Args:
        url (str): Target URL
        data (str): Request data
        headers (dict): HTTP headers
        request_type (str): Request type ('get' or 'post')

    Returns:
        requests.Response: HTTP response object
    """
    try:
        if headers is None:
            headers = {}

        # Get HMAC key from cache
        hmac_key = get_hmac_key()
        if not hmac_key:
            raise ValueError("Failed to retrieve HMAC key from SSM")

        # Generate signature for request data
        request_data = data or ''
        headers['X-Signature'] = generate_hmac_signature(hmac_key, request_data)
        headers['Content-Type'] = 'application/json'

        # Send request with timeout
        if request_type == 'get':
            response = requests.get(url, headers=headers, data=data, timeout=30)
        elif request_type == 'post':
            response = requests.post(url, headers=headers, data=data, timeout=30)
        else:
            raise ValueError(f"Unknown request type: {request_type}")

        response.raise_for_status()  # Raise exception for bad status codes
        return response
    except requests.exceptions.Timeout:
        st.error(f"Request to {url} timed out")
        return None
    except requests.exceptions.RequestException as e:
        st.error(f"Request failed: {str(e)}")
        return None
    except Exception as e:
        st.error(f"Error in send_signed_request: {str(e)}")
        return None

def get_clients():
    """
    Retrieve list of clients from the API.

    Returns:
        list: List of client dictionaries
    """
    try:
        response = send_signed_request(CLIENTS_API, request_type='get')
        if response and response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        st.error(f"Error retrieving clients: {str(e)}")
        return []

# Add the signature to the 'X-Signature' header for the request

class ProgressPercentage(object):
    def __init__(self, filename):
        self._filename = filename
        self._size = float(os.path.getsize(filename))
        self._seen_so_far = 0
        self._lock = threading.Lock()

    def __call__(self, bytes_amount):
        # To simplify, assume this is hooked up to a single filename.
        with self._lock:
            self._seen_so_far += bytes_amount
            percentage = (self._seen_so_far / self._size) * 100
            sys.stdout.write(
                "\r%s  %s / %s  (%.2f%%)" % (
                    self._filename, self._seen_so_far, self._size,
                    percentage))
            sys.stdout.flush()




def create_album_zip(client_name, album_name):
    album_path = os.path.join(BASE_DIR, client_name, 'albums', album_name)
    zip_path = os.path.join(BASE_DIR, client_name, 'albums', f"{album_name}.zip")

    # Creating a zip file for the album
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(album_path):
            for file in files:
                file_path = os.path.join(root, file)
                zipf.write(file_path, os.path.relpath(file_path, album_path))
    
    return zip_path




def list_albums(client_name):
    """List all albums for a given client."""
    path = os.path.join(BASE_DIR, client_name, 'albums', '*')
    return [os.path.basename(album) for album in glob.glob(path)]

def display_album_photos(client_name, album_name):
    """Display all photos in a given album."""
    album_path = os.path.join(BASE_DIR, client_name, 'albums', album_name, '*')
    photo_paths = glob.glob(album_path)
    for photo_path in photo_paths:
        st.image(photo_path, caption=os.path.basename(photo_path), use_column_width=True)

def upload_album_to_s3(client_name, album_name):
    # Upload individual photos
    album_path = os.path.join(BASE_DIR, client_name, 'albums', album_name, '*')
    photo_paths = glob.glob(album_path)
    
    for photo_path in photo_paths:
        if photo_path.endswith('.zip'):
            continue  # Skip the ZIP file in the photo upload loop
        upload_file_to_s3(photo_path, client_name, album_name)

    # Upload the ZIP file
    zip_path = os.path.join(BASE_DIR, client_name, 'albums', f"{album_name}.zip")
    if os.path.exists(zip_path):
        upload_file_to_s3(zip_path, client_name, album_name, is_zip=True)
    else:
        st.error("ZIP file does not exist. Please prepare the album first.")

def validate_s3_key_name(key_name):
    """
    Sanitize S3 key name to prevent path traversal and invalid characters.

    Args:
        key_name (str): Original key name

    Returns:
        str: Sanitized key name
    """
    if not key_name:
        return ''

    # Remove path traversal attempts
    key_name = key_name.replace('..', '_')

    # Replace invalid characters
    invalid_chars = ['\\', '<', '>', ':', '"', '|', '?', '*']
    for char in invalid_chars:
        key_name = key_name.replace(char, '_')

    return key_name.strip('/')

def upload_file_to_s3(file_path, client_name, album_name, is_zip=False):
    """
    Upload file to S3 with optimized multipart configuration.

    Args:
        file_path (str): Local file path
        client_name (str): Client name
        album_name (str): Album name
        is_zip (bool): Whether the file is a zip archive

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Retrieve bucket name from environment or SSM
        bucket_name = os.environ.get('S3_BUCKET_NAME') or get_secret_from_ssm(f"/album-manager/{ENV}/s3_bucket_name")
        if not bucket_name:
            st.error("Missing S3_BUCKET_NAME configuration")
            return False

        file_name = os.path.basename(file_path)

        # Build S3 key with sanitized names
        if is_zip:
            object_name = f'clients/{validate_s3_key_name(client_name)}/albums/{validate_s3_key_name(album_name)}.zip'
        else:
            object_name = f'clients/{validate_s3_key_name(client_name)}/albums/{validate_s3_key_name(album_name)}/{validate_s3_key_name(file_name)}'

        # PERFORMANCE OPTIMIZATION: Multipart upload configuration
        # Increased threshold from 25KB to 5MB (AWS recommended minimum)
        # This reduces the number of API calls and improves performance
        config = boto3.s3.transfer.TransferConfig(
            multipart_threshold=1024 * 1024 * 5,  # 5MB (was 25KB - MAJOR IMPROVEMENT)
            max_concurrency=10,
            multipart_chunksize=1024 * 1024 * 5,  # 5MB (was 25KB - MAJOR IMPROVEMENT)
            use_threads=True
        )

        # Determine content type
        content_type = 'application/zip' if is_zip else 'image/jpeg'
        if file_name.lower().endswith('.png'):
            content_type = 'image/png'
        elif file_name.lower().endswith('.gif'):
            content_type = 'image/gif'

        # SECURITY FIX: Removed 'ACL': 'public-read' - files are now private
        # Use presigned URLs for sharing instead of public access
        S3_CLIENT.upload_file(
            file_path,
            bucket_name,
            object_name,
            ExtraArgs={
                'ContentType': content_type,
                'ServerSideEncryption': 'AES256'  # Enable encryption at rest
            },
            Config=config,
            Callback=ProgressPercentage(file_path)
        )

        st.success(f"Uploaded {file_name} to S3 (private, encrypted)")
        return True

    except ClientError as e:
        st.error(f"Failed to upload {file_name} to S3: {e}")
        return False
    except Exception as e:
        st.error(f"Unexpected error uploading {file_name}: {str(e)}")
        return False


# moved to other fun
# def upload_album_to_s3(client_name, album_name):
#     """Upload all photos in the selected album to S3."""
#     album_path = os.path.join(BASE_DIR, client_name, 'albums', album_name, '*')
#     photo_paths = glob.glob(album_path)
    
#     for photo_path in photo_paths:
#         file_name = os.path.basename(photo_path)
#         object_name = f'clients/{validate_s3_key_name(client_name)}/albums/{validate_s3_key_name(album_name)}/{validate_s3_key_name(file_name)}'
#         try:
#             with open(photo_path, 'rb') as f:
#                 s3.upload_fileobj(f, bucket_name, object_name)
#             st.success(f"Uploaded {file_name} to S3")
#         except ClientError as e:
#             st.error(f"Failed to upload {file_name} to S3: {e}")

def main():
    st.title("Photo Album Manager")

    # Select client
    clients = get_clients()  # Assume this is a function that retrieves client names
    client_name = st.selectbox("Select Client", clients)

    # Select album
    if client_name:
        albums = list_albums(client_name)
        album_name = st.selectbox("Select Album", albums)

        # Display album photos
        if album_name:
            st.write(f"Displaying photos from {album_name}:")
            display_album_photos(client_name, album_name)

            # Upload button
            if st.button(f"Upload {album_name} to S3"):
                upload_album_to_s3(client_name, album_name)

if __name__ == "__main__":
    main()
