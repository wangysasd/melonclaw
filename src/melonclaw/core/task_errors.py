"""任务中心的稳定业务错误，不包含内部异常或凭据。"""


class TaskError(ValueError):
    def __init__(self, code: str, message: str, status: int = 409):
        self.error_code = code
        self.status_code = status
        super().__init__(message)


def task_not_found():
    return TaskError("task_not_found", "任务或运行不存在，或当前身份无权访问。", 404)
