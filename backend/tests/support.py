"""测试引导：在导入 src.* 之前注入 FastAPI / SQLAlchemy / pydantic 的最小替身。

线上环境使用 requirements.txt 中的真实依赖；这里仅为离线环境验证服务层业务
规则，替身只实现项目实际用到的接口。
"""

import sys
import types


def _install():
    if 'fastapi' in sys.modules and hasattr(sys.modules['fastapi'], '__test_stub__'):
        return

    # ---------- SQLAlchemy ----------
    sqlalchemy = types.ModuleType('sqlalchemy')
    sqlalchemy.__test_stub__ = True

    class MappedColumn:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.test_attr = None
            if kwargs.get('primary_key'):
                self.default = None

        def __set_name__(self, owner, name):
            self.test_attr = name

        def __eq__(self, other):
            return (self, other)

        def __hash__(self):
            return id(self)

    def mapped_column(*args, **kwargs):
        return MappedColumn(*args, **kwargs)

    class _Mapped:
        def __class_getitem__(cls, item):
            class _Column:
                def __eq__(self_inner, other):
                    return (_Column, other)
            return _Column()

    sqlalchemy.Mapped = _Mapped
    sqlalchemy.mapped_column = mapped_column
    sqlalchemy.JSON = 'JSON'
    sqlalchemy.String = lambda *a, **k: ('String', a, k)
    sqlalchemy.Text = 'Text'
    sqlalchemy.ForeignKey = lambda target, *a, **k: ('FK', target)

    class FakeSelect:
        def __init__(self, model):
            self.model = model
            self.filters = []

        def where(self, *args):
            self.filters.extend(args)
            return self

        def with_for_update(self, skip_locked=False):
            return self

        def order_by(self, *args):
            return self

        def limit(self, n):
            return self

        def outerjoin(self, *args):
            return self

    sqlalchemy.select = lambda *models: FakeSelect(models[0])
    sqlalchemy.update = lambda model: FakeSelect(model)
    sqlalchemy.func = types.SimpleNamespace(count=lambda: 'count')

    class FakeEngine:
        def begin(self):
            class Ctx:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    return False

                def execute(self_inner, statement):
                    return None
            return Ctx()

    sqlalchemy.create_engine = lambda *a, **k: FakeEngine()
    sqlalchemy.inspect = lambda engine: types.SimpleNamespace(
        get_table_names=lambda: [], get_columns=lambda table: [])
    sqlalchemy.text = lambda sql: sql
    sqlalchemy.exc = types.ModuleType('sqlalchemy.exc')

    class IntegrityError(Exception):
        pass

    sqlalchemy.exc.IntegrityError = IntegrityError

    orm = types.ModuleType('sqlalchemy.orm')

    class DeclarativeBase:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class _Mapped:
        def __class_getitem__(cls, item):
            return item

    orm.DeclarativeBase = DeclarativeBase
    orm.Mapped = _Mapped
    orm.mapped_column = mapped_column
    orm.sessionmaker = lambda **k: None

    class Session:
        pass

    orm.Session = Session
    sqlalchemy.orm = orm

    engine_pkg = types.ModuleType('sqlalchemy.engine')
    sqlalchemy.engine = engine_pkg

    # ---------- FastAPI ----------
    fastapi = types.ModuleType('fastapi')
    fastapi.__test_stub__ = True

    class HTTPException(Exception):
        def __init__(self, status_code, detail):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    fastapi.HTTPException = HTTPException

    class APIRouter:
        def __init__(self, *args, **kwargs):
            self.routes = []
            self.prefix = kwargs.get('prefix', '')

        def _register(self, path, methods, func):
            class Route:
                pass
            route = Route()
            route.path = self.prefix + path
            route.methods = set(methods)
            route.func = func
            self.routes.append(route)
            return func

        def get(self, path, *args, **kwargs):
            return lambda func: self._register(path, {'GET'}, func)

        def post(self, path, *args, **kwargs):
            return lambda func: self._register(path, {'POST'}, func)

        def put(self, path, *args, **kwargs):
            return lambda func: self._register(path, {'PUT'}, func)

        def delete(self, path, *args, **kwargs):
            return lambda func: self._register(path, {'DELETE'}, func)

        def patch(self, path, *args, **kwargs):
            return lambda func: self._register(path, {'PATCH'}, func)

        def include_router(self, router):
            self.routes.extend(router.routes)

    class FastAPI:
        def __init__(self, *args, **kwargs):
            self.routes = []
            self.user_middleware = []

        def add_middleware(self, *args, **kwargs):
            self.user_middleware.append(args)

        def middleware(self, *args, **kwargs):
            return lambda func: func

        def add_exception_handler(self, *args, **kwargs):
            pass

        def include_router(self, router):
            self.routes.extend(router.routes)

    fastapi.FastAPI = FastAPI
    fastapi.Request = type('Request', (), {})
    fastapi.Query = lambda default=None, **kwargs: default
    fastapi.APIRouter = APIRouter
    fastapi.Depends = lambda func=None: func

    class _Bearer:
        def __init__(self, *args, **kwargs):
            pass

    fastapi.HTTPBearer = _Bearer

    security = types.ModuleType('fastapi.security')
    security.HTTPBearer = _Bearer

    class HTTPAuthorizationCredentials:
        pass

    security.HTTPAuthorizationCredentials = HTTPAuthorizationCredentials
    fastapi.security = security

    responses = types.ModuleType('fastapi.responses')

    class Response:
        def __init__(self, content='', media_type=None, headers=None):
            self.content = content

    class JSONResponse(Response):
        def __init__(self, content=None, status_code=200):
            super().__init__(content)
            self.status_code = status_code

    responses.Response = Response
    responses.JSONResponse = JSONResponse
    fastapi.responses = responses

    # ---------- pydantic ----------
    pydantic = types.ModuleType('pydantic')
    pydantic.__test_stub__ = True

    class FieldInfo:
        def __init__(self, default=None, **kwargs):
            self.default = default
            self.kwargs = kwargs

    def Field(default=None, **kwargs):
        return FieldInfo(default, **kwargs)

    class ConfigDict(dict):
        pass

    class BaseModel:
        def __init__(self, **data):
            annotations = {}
            for klass in reversed(type(self).__mro__):
                annotations.update(getattr(klass, '__annotations__', {}))
            for name in annotations:
                if name in data:
                    setattr(self, name, data[name])
                else:
                    default = getattr(type(self), name, None)
                    setattr(self, name, default.default if isinstance(default, FieldInfo) else default)

        def model_dump(self):
            return self.__dict__.copy()

    pydantic.BaseModel = BaseModel
    pydantic.Field = Field
    pydantic.ConfigDict = ConfigDict
    pydantic.constr = lambda **kwargs: str

    # ---------- redis / jwt（服务模块导入期需要） ----------
    redis_pkg = types.ModuleType('redis')

    class RedisError(Exception):
        pass

    class FakeRedis:
        @classmethod
        def from_url(cls, *a, **k):
            return cls()

        def pipeline(self):
            class Pipe:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def lpush(self, *a):
                    return self

                def ltrim(self, *a):
                    return self

                def execute(self):
                    return []
            return Pipe()

        def brpop(self, *a, **k):
            return None

        def ping(self):
            return True

    redis_pkg.Redis = FakeRedis
    redis_pkg.RedisError = RedisError
    redis_pkg.Redis = FakeRedis
    redis_pkg.from_url = lambda *a, **k: FakeRedis()

    jwt_pkg = types.ModuleType('jwt')

    class InvalidTokenError(Exception):
        pass

    jwt_pkg.InvalidTokenError = InvalidTokenError

    for name, module in {
        'sqlalchemy': sqlalchemy, 'sqlalchemy.orm': orm, 'sqlalchemy.exc': sqlalchemy.exc,
        'sqlalchemy.engine': engine_pkg, 'fastapi': fastapi, 'fastapi.responses': responses,
        'fastapi.security': security,
        'pydantic': pydantic, 'redis': redis_pkg, 'jwt': jwt_pkg,
    }.items():
        sys.modules[name] = module


_install()
