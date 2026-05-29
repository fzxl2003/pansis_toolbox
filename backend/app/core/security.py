from fastapi import Request


def get_current_user_id(request: Request) -> str:
    return request.headers.get("x-user-id", "local")
