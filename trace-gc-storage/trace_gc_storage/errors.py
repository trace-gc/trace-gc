class ContextStoreError(Exception):
    pass

class UnknownContextError(ContextStoreError):
    def __init__(self, context_id: str):
        super().__init__(f"Context not found: {context_id}")
        self.context_id = context_id

class ExpiredContextError(ContextStoreError):
    def __init__(self, context_id: str):
        super().__init__(f"Context has expired: {context_id}")
        self.context_id = context_id

class ContextPurgedError(ContextStoreError):
    def __init__(self, context_id: str):
        super().__init__(f"Context has been purged: {context_id}")
        self.context_id = context_id

class ReceiptNotFoundError(ContextStoreError):
    def __init__(self, context_id: str, node_id: str):
        super().__init__(f"Receipt {node_id} not found in context {context_id}")
        self.context_id = context_id
        self.node_id = node_id

class IdempotencyConflictError(ContextStoreError):
    def __init__(self, context_id: str, request_id: str):
        super().__init__(f"Idempotency conflict for request {request_id} in context {context_id}")
        self.context_id = context_id
        self.request_id = request_id
