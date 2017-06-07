
# Convenience interface for unlocking dlock13 door-locks
# Handles MQTT communication, checking result/errors, with a request-response interface

import paho.mqtt.client as mqtt

import Queue as queue
import numbers
import threading
import time, os

import logging
logging.basicConfig()
log_level = os.environ.get('DLOCK13_LOGLEVEL')
logger = logging.getLogger('dlock13')
if log_level:
  level = getattr(logging, log_level.upper())
  logger.setLevel(level)

def mqtt_thread(inqueue, doors, host, port):

    state = {
        'open_request': None,
        'waiting_until': None, # TODO: implement timeout logic
    }

    def on_connect(client, userdata, flags, rc):
        # subscribe in on_connect, so that reconnects will re-subscribe
        for name, basetopic in doors.items():
            client.subscribe(basetopic+'/error')
            client.subscribe(basetopic+'/isopen')
            client.subscribe(basetopic+'/openuntil')

    # TODO: receive and report incoming errors nicely
    def on_message(client, userdata, msg):
        print(msg.topic+" "+str(msg.payload))

        pieces = msg.topic.split('/') 
        outport = pieces[-1]
        base = pieces[:-1].join('/')

        door = None
        for name, topicbase in doors.items():
            if base == topicbase:
                door = name

        print outport, base, door, state

        if state.get('open_request') and door and outport == 'openuntil':
            state['open_request'].response.put({'door': door, 'openuntil': msg.payload })
            state['open_request'] = None
        elif outport == 'isopen':
            pass # unused
        else:
            print 'ignored message'

    client = mqtt.Client(userdata=None)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port, 60)
    running = True
    while running:
        try:
            request = inqueue.get()
            action = request['action']
            if action == 'open':
                door = request['door']
                state['open_request'] = request 
            elif action == 'quit':
                running = False
            else:
                raise ValueError("Unsupported request action: %s" % action)
        except queue.Empty, e:
            pass # no message right now
        except Exception, e:
            # TODO: use logging instead
            print 'dlock13-mqtt_thread', e

        client.loop(timeout=0.1)

class Doorlock(object):
    def __init__(self, doors, host='localhost', port=1883):
        self._doors = doors # { 'name': 'prefix/door/$name', ..}

        self._request_queue = queue.Queue()

        args = [self._request_queue, self._doors, host, port]
        self._thread = threading.Thread(target=mqtt_thread, name="dlock13-DoorLock-mqtt", args=args)
        self._thread.start()

    def __del__(self):
        self._request_queue.put({'action': 'quit'})
        self._thread.join()

    # blocking until lock confirms it was opened
    # returns: Number, UTC Unix timestamp for when door will lock again
    def open(self, name, duration, timeout=0.5):
        if not self._doors.get(name, None):
            raise ValueError("Unknown door %s" % s)
        if not (isinstance(duration, numbers.Number)):
            raise ValueError("Duration must be a number")
        if not (duration > 0.0 and duration < 600):
            raise ValueError("Open duration %d outside supported range" % duration)

        open_at = time.time() 

        logging.info('open() door=%s, at=%d' % (name, open_at))

        # create a temporary queue, where thread will put our response
        response_queue = queue.Queue()

        # fire the request
        request = { 'action': 'open', 'door': name, 'duration': duration, 'timeout': timeout, 'reponse': response_queue }
        self._request_queue.put(request)

        # wait for response
        print 'waiting'
        response = response_queue.get(block=True, timeout=timeout*2)

        # sanity-check post-conditions
        until = response['openuntil']
        if not (isinstance(until, numbers.Number)):
            raise Exception("openuntil response not a number")
        if not (until > open_at):
            raise Exception("openuntil response lower than time when opened! %d < %d" %(until, open_at))

        logging.info('open() success door=%s, until=%d' % (name, until))
        return until
