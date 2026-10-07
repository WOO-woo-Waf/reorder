from __future__ import annotations

import json
import sys
from pydantic import ValidationError

from reorder_engine.application.errors import EngineError
from reorder_engine.application.facade import EngineFacade

MAX_FRAME = 1024 * 1024


class JsonRpcServer:
    def __init__(self, facade: EngineFacade):
        self.facade = facade

    def handle(self, frame: bytes) -> dict:
        request_id = None
        try:
            value = json.loads(frame, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
                raise EngineError("INVALID_REQUEST", "请求格式不正确。")
            request_id = value.get("id")
            if (not isinstance(request_id, (str, int)) or isinstance(request_id, bool) or
                    not isinstance(value.get("method"), str) or not isinstance(value.get("params", {}), dict)):
                request_id = None
                raise EngineError("INVALID_REQUEST", "请求 ID、方法或参数不正确。")
            result = self.facade.dispatch(value["method"], value.get("params", {}))
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except ValidationError:
            return self.error(request_id, -32602, "INVALID_PARAMS", "参数类型或范围不正确。")
        except (ValueError, UnicodeError):
            return self.error(request_id, -32700, "PARSE_ERROR", "请求不是合法 JSON。")
        except EngineError as exc:
            codes = {"METHOD_NOT_FOUND": -32601, "INVALID_REQUEST": -32600, "INVALID_PARAMS": -32602}
            return self.error(request_id, codes.get(exc.code, -32000), exc.code, self.facade.secrets.redact(str(exc)))
        except Exception:
            print("engine: request failed", file=sys.stderr)
            return self.error(request_id, -32603, "INTERNAL_ERROR", "引擎操作失败，文件不会自动重做。")

    @staticmethod
    def error(request_id, code: int, business_code: str, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message, "data": {"code": business_code}}}

    def serve(self, reader, writer) -> None:
        while frame := reader.readline(MAX_FRAME + 1):
            if len(frame) > MAX_FRAME:
                while frame and not frame.endswith(b"\n"):
                    frame = reader.readline(MAX_FRAME + 1)
                response = self.error(None, -32600, "FRAME_TOO_LARGE", "请求超过 1 MiB。")
            else:
                response = self.handle(frame)
            encoded = json.dumps(response, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) > MAX_FRAME:
                response = self.error(response.get("id"), -32000, "RESPONSE_TOO_LARGE", "结果过大，请减少单批文件或分页读取。")
                encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
            writer.write(encoded + b"\n")
            writer.flush()
