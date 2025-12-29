import multiprocessing
from ctypes import c_bool, c_char

class SharedState:
    def __init__(self):
        # Voice -> Vision Command Queue
        self.command_queue = multiprocessing.Queue()
        
        # System Active Flag
        self.system_active = multiprocessing.Value(c_bool, True)
        
        # ACTIVE CONTEXT (The missing piece!)
        # Stores 'discord', 'browser', 'spotify', or 'desktop'
        self.active_context = multiprocessing.Array(c_char, 50)

    def get_context(self):
        return self.active_context.value.decode('utf-8')

    def set_context(self, context_str):
        # Truncate to 50 chars to fit buffer
        self.active_context.value = context_str[:49].encode('utf-8')