import streamlit as st
import boto3
from boto3.dynamodb.conditions import Key
import uuid
from botocore.exceptions import ClientError

# Initialize DynamoDB with connection pooling
dynamodb = boto3.resource('dynamodb')

# Performance note: scan() operations are expensive and slow
# Consider adding GSI (Global Secondary Index) for frequent queries

def list_clients(limit=100, last_evaluated_key=None):
    """
    List clients with pagination to avoid expensive full table scans.

    Args:
        limit (int): Maximum number of items to return
        last_evaluated_key (dict): Key to start scan from (for pagination)

    Returns:
        tuple: (items list, last_evaluated_key for next page)
    """
    try:
        table = dynamodb.Table('Clients')
        scan_kwargs = {
            'Limit': limit
        }
        if last_evaluated_key:
            scan_kwargs['ExclusiveStartKey'] = last_evaluated_key

        response = table.scan(**scan_kwargs)
        return response.get('Items', []), response.get('LastEvaluatedKey')
    except ClientError as e:
        st.error(f"Error listing clients: {str(e)}")
        return [], None

def list_orders(limit=100, last_evaluated_key=None):
    """
    List orders with pagination.

    Args:
        limit (int): Maximum number of items to return
        last_evaluated_key (dict): Key to start scan from (for pagination)

    Returns:
        tuple: (items list, last_evaluated_key for next page)
    """
    try:
        table = dynamodb.Table('PayPalWebhooks')  # Updated to match serverless.yml
        scan_kwargs = {
            'Limit': limit
        }
        if last_evaluated_key:
            scan_kwargs['ExclusiveStartKey'] = last_evaluated_key

        response = table.scan(**scan_kwargs)
        return response.get('Items', []), response.get('LastEvaluatedKey')
    except ClientError as e:
        st.error(f"Error listing orders: {str(e)}")
        return [], None

def list_albums(limit=100, last_evaluated_key=None):
    """
    List albums with pagination.

    Args:
        limit (int): Maximum number of items to return
        last_evaluated_key (dict): Key to start scan from (for pagination)

    Returns:
        tuple: (items list, last_evaluated_key for next page)
    """
    try:
        table = dynamodb.Table('AlbumDetails')  # Updated to match actual table name
        scan_kwargs = {
            'Limit': limit
        }
        if last_evaluated_key:
            scan_kwargs['ExclusiveStartKey'] = last_evaluated_key

        response = table.scan(**scan_kwargs)
        return response.get('Items', []), response.get('LastEvaluatedKey')
    except ClientError as e:
        st.error(f"Error listing albums: {str(e)}")
        return [], None

def insert_client(client_name, email):
    """
    Insert a new client into DynamoDB.

    Args:
        client_name (str): Client name
        email (str): Client email

    Returns:
        dict: Response from DynamoDB
    """
    # Validate inputs
    if not client_name or not email:
        raise ValueError("Client name and email are required")

    if '@' not in email:
        raise ValueError(f"Invalid email format: {email}")

    try:
        table = dynamodb.Table('Clients')
        client_id = str(uuid.uuid4())  # Generate unique ID

        import time
        response = table.put_item(
            Item={
                'clientID': client_id,
                'clientName': client_name,
                'email': email,
                'createdAt': int(time.time())
            }
        )
        return response
    except ClientError as e:
        st.error(f"Error inserting client: {str(e)}")
        raise

def insert_album(client_id, album_name):
    """
    Insert a new album into DynamoDB.

    Args:
        client_id (str): Client ID
        album_name (str): Album name

    Returns:
        dict: Response from DynamoDB
    """
    # Validate inputs
    if not client_id or not album_name:
        raise ValueError("Client ID and album name are required")

    try:
        table = dynamodb.Table('AlbumDetails')
        album_id = str(uuid.uuid4())  # Generate unique ID

        import time
        response = table.put_item(
            Item={
                'albumID': album_id,
                'clientID': client_id,
                'albumName': album_name,
                'createdAt': int(time.time())
            }
        )
        return response
    except ClientError as e:
        st.error(f"Error inserting album: {str(e)}")
        raise

def main():
    """
    Main Streamlit application for DynamoDB management.
    """
    st.title("DynamoDB Management Dashboard")

    # Add pagination state
    if 'clients_page_key' not in st.session_state:
        st.session_state.clients_page_key = None
    if 'orders_page_key' not in st.session_state:
        st.session_state.orders_page_key = None
    if 'albums_page_key' not in st.session_state:
        st.session_state.albums_page_key = None

    tab1, tab2, tab3 = st.tabs(["Clients", "PayPal Orders", "Albums"])

    with tab1:
        st.header("Clients")

        # List clients with pagination
        clients, next_key = list_clients(limit=100, last_evaluated_key=st.session_state.clients_page_key)

        if clients:
            for client in clients:
                st.json(client)  # Display as JSON for better formatting
        else:
            st.info("No clients found")

        # Pagination controls
        if next_key:
            if st.button("Load More Clients"):
                st.session_state.clients_page_key = next_key
                st.rerun()

        # Insert client form
        st.subheader("Add New Client")
        with st.form("Insert Client"):
            client_name = st.text_input("Client Name", help="Enter the client's full name")
            email = st.text_input("Email", help="Enter a valid email address")
            submit_button = st.form_submit_button("Insert Client")
            if submit_button:
                try:
                    insert_client(client_name, email)
                    st.success(f"Client '{client_name}' inserted successfully!")
                    st.session_state.clients_page_key = None  # Reset pagination
                except Exception as e:
                    st.error(f"Failed to insert client: {str(e)}")

    with tab2:
        st.header("PayPal Orders")

        # List orders with pagination
        orders, next_key = list_orders(limit=100, last_evaluated_key=st.session_state.orders_page_key)

        if orders:
            for order in orders:
                st.json(order)  # Display as JSON for better formatting
        else:
            st.info("No orders found")

        # Pagination controls
        if next_key:
            if st.button("Load More Orders"):
                st.session_state.orders_page_key = next_key
                st.rerun()

    with tab3:
        st.header("Albums")

        # List albums with pagination
        albums, next_key = list_albums(limit=100, last_evaluated_key=st.session_state.albums_page_key)

        if albums:
            for album in albums:
                st.json(album)  # Display as JSON for better formatting
        else:
            st.info("No albums found")

        # Pagination controls
        if next_key:
            if st.button("Load More Albums"):
                st.session_state.albums_page_key = next_key
                st.rerun()

        # Insert album form
        st.subheader("Add New Album")
        with st.form("Insert Album"):
            client_id = st.text_input("Client ID", help="Enter the client's unique ID")
            album_name = st.text_input("Album Name", help="Enter the album name")
            submit_button = st.form_submit_button("Insert Album")
            if submit_button:
                try:
                    insert_album(client_id, album_name)
                    st.success(f"Album '{album_name}' inserted successfully!")
                    st.session_state.albums_page_key = None  # Reset pagination
                except Exception as e:
                    st.error(f"Failed to insert album: {str(e)}")

if __name__ == "__main__":
    main()
