import os
import time
import uuid
import json
import base64
import asyncio
import socketio
import datetime
import threading
import re



t_sleep = 45
reg_date = "(\w{4}-\w{2}-\w{2})[A-z](\w.*)"
datetime_format_clean = "%Y-%m-%d %H:%M:%S.%f"


class PyGPT:
    def __init__(self, session_token, timeout=120, bypass_node='https://gpt.pawan.krd', pro_account=False,
                 name="default"):
        self.ready = False
        self.socket = socketio.AsyncClient()
        self.socket.on('connect', self.on_connect)
        self.socket.on('disconnect', self.on_disconnect)
        self.socket.on('serverMessage', print)
        self.session_token = session_token
        self.conversations = []
        self.pro_account = pro_account
        self.expires = datetime.datetime.now()
        self.auth = None
        self.timeout = timeout
        self.bypass_node = bypass_node
        self.pause_token_checks = True
        self.filepath = f'{name}-PyGPT.json'
        self.load()
        asyncio.create_task(self.cleanup_conversations())
        self.stop_thread = [False]
        self.threadazzo = threading.Thread(target=self.save_interval)
        self.threadazzo.start()

    def save(self):
        data = {
            'session_token': self.session_token,
            'conversations': self.conversations,
            'proAccount': self.pro_account,
            'expires': self.expires.isoformat(),
            'auth': self.auth,
        }
        for conversation in self.conversations:
            conversation['conversation_id'] = str(conversation['conversation_id'])
            conversation['parent_id'] = str(conversation['parent_id'])
            last_active = conversation['last_active']
            if not isinstance(last_active, str) :
              last_active = last_active.isoformat()

            conversation['last_active'] = last_active
        with open(self.filepath, 'w') as f:
            json.dump(data, f, indent=4)

    def load(self):
        if os.path.exists(self.filepath):
            with open(self.filepath, 'r') as f:
                data = json.load(f)
                self.session_token = data['session_token']
                self.conversations = data['conversations']
                self.pro_account = data['proAccount']
                self.expires = datetime.datetime.fromisoformat(data['expires'])
                self.auth = data['auth']
                time.sleep(1)
                self.pause_token_checks = False
                if self.auth:
                    self.ready = True
        else:
            time.sleep(1)
            self.pause_token_checks = False

    def save_interval(self):
        while not self.stop_thread[0] :
            time.sleep(t_sleep)
            self.save()

    async def connect(self):
        await self.socket.connect(f'{self.bypass_node}/?client=python&version=1.0.5&versionCode=105')

    async def disconnect(self):
        await self.socket.disconnect()
        self.stop_thread[0] = True
        print('Closing threads ...wait')
        self.threadazzo.join()
        print('[+] Thread exit')

    def on_connect(self):
        print('Connected to server')
        asyncio.create_task(self.check_tokens())

    def on_disconnect(self):
        print('Disconnected from server')
        self.ready = False

    async def check_tokens(self):
        while True:
            if self.pause_token_checks:
                await asyncio.sleep(0.5)
                continue
            self.pause_token_checks = True
            now = datetime.datetime.utcnow()
            offset = datetime.timedelta(minutes=2)
            if self.expires < (now - offset) or not self.auth:
                print('Token expired, getting new token')
                await self.get_tokens()
            self.pause_token_checks = False
            await asyncio.sleep(0.5)

    async def cleanup_conversations(self, t_sleep=60):
        
        def check_date(last_active):
            rets = None
            last_active_str = str(last_active)
            re_match = re.match(reg_date, last_active_str)
            if re_match :
                d1, d2 = re_match.groups()
                rets = datetime.datetime.strptime("{} {}".format(d1, d2), datetime_format_clean)
            return rets

        while True:
            await asyncio.sleep(t_sleep)
            now = datetime.datetime.now()
            output = []
            for c in self.conversations :
              last_active = c['last_active']
              tmp_last_active = check_date(last_active)
              if tmp_last_active != None :
                last_active = tmp_last_active
                c['last_active'] = last_active

              if now - last_active < datetime.timedelta(minutes=2) :
                output.append(c)
            self.conversations = output

    def add_conversation(self, conv_id):
        conversation = {
            'id': conv_id,
            'conversation_id': None,
            'parent_id': uuid.uuid4(),
            'last_active': datetime.datetime.now()
        }
        self.conversations.append(conversation)
        self.save()
        return conversation

    def get_conversation_by_id(self, conv_id):
        conversation = next((c for c in self.conversations if c['id'] == conv_id), None)
        if conversation is None:
            conversation = self.add_conversation(conv_id)
        else:
            conversation['last_active'] = datetime.datetime.now()
        return conversation

    async def wait_for_ready(self):
        while not self.ready:
            await asyncio.sleep(0.025)
        print('Ready!!')

    async def ask(self, prompt, conv_id='default'):
        if not self.auth or not self.validate_token():
            print('Token expired, getting new token')
            await self.get_tokens()
        conversation = self.get_conversation_by_id(conv_id)

        eventName = 'askQuestion' if not self.pro_account else 'askQuestionPro'

        # Fix for timeout issue by Ulysses0817: https://github.com/Ulysses0817
        data = await self.socket.call(event=eventName, data={
            'prompt': prompt,
            'parentId': str(conversation['parent_id']),
            'conversationId': str(conversation['conversation_id']),
            'auth': self.auth
        }, timeout=self.timeout)

        if 'error' in data:
            print(f'Error: {data["error"]}')
        conversation['parent_id'] = data['messageId']
        conversation['conversation_id'] = data['conversationId']
        return data['answer']

    def validate_token(self):
        if not self.auth:
            return False
        parsed = json.loads(base64.b64decode(f'{self.auth.split(".")[1]}==').decode())
        return datetime.datetime.now() <= datetime.datetime.fromtimestamp(parsed['exp'])

    async def get_tokens(self):
        print('Getting tokens...')
        await asyncio.sleep(1)
        # Fix for timeout issue by Ulysses0817: https://github.com/Ulysses0817
        data = await self.socket.call(event='getSession', data=self.session_token, timeout=self.timeout)

        if 'error' in data:
            print(f'Error getting session: {data["error"]}')
        else:
            self.auth = data['auth']
            self.expires = datetime.datetime.strptime(data['expires'], '%Y-%m-%dT%H:%M:%S.%fZ')
            self.session_token = data['sessionToken']
            self.ready = True
            self.save()
