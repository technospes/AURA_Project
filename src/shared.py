import multiprocessing
from ctypes import c_bool, c_char

class SharedState:
    def __init__(self):
        self.command_queue = multiprocessing.Queue()
        self.system_active = multiprocessing.Value(c_bool, True)
    
        self.active_context = multiprocessing.Array(c_char, 50)

    def get_context(self):
        return self.active_context.value.decode('utf-8')

    def set_context(self, context_str):
        self.active_context.value = context_str[:49].encode('utf-8')