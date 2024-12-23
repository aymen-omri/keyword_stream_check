import pika

def delete_and_recreate_queue(queue_name):
    # Connect to RabbitMQ
    connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
    channel = connection.channel()
    
    # Delete the queue (if it exists)
    channel.queue_delete(queue=queue_name)
    print(f"Queue '{queue_name}' has been deleted.")
    
    # Recreate the queue
    channel.queue_declare(queue=queue_name, durable=True)
    print(f"Queue '{queue_name}' has been recreated.")
    
    # Close the connection
    connection.close()

# Delete and recreate the queue before starting
delete_and_recreate_queue('transcription_tasks')
