import json
import pika

def send_json_objects_to_queue(json_file_path):
    """
    Reads a JSON file and sends its objects to a RabbitMQ queue.
    """
    # Load the JSON file
    try:
        with open(json_file_path, 'r') as json_file:
            json_data = json.load(json_file)
    except Exception as e:
        print(f"Error loading JSON file: {e}")
        return
    
    # Connect to RabbitMQ
    connection = pika.BlockingConnection(pika.ConnectionParameters('rabbitmq'))
    channel = connection.channel()
    
    # Declare a queue
    channel.queue_declare(queue='transcription_tasks', durable=True)
    
    # Send each JSON object as a message
    if isinstance(json_data, list):
        for item in json_data:
            message = json.dumps(item)  # Convert JSON object to string
            channel.basic_publish(
                exchange='',
                routing_key='transcription_tasks',
                body=message,
                properties=pika.BasicProperties(delivery_mode=2)  # Make messages persistent
            )
            print(f"Sent JSON object: {message}")
    else:
        print("JSON file does not contain a list of objects.")
    
    # Close the connection
    connection.close()

# Specify the path to your JSON file
json_file_path = 'iptv_streams.json'

# Push JSON objects to RabbitMQ
send_json_objects_to_queue(json_file_path)
