# Muotti

Muotti maps an object onto another object.
It reads the fields of a source, converts them to the types the destination expects, and builds or updates that destination.

Muotti knows nothing about any particular kind of object, and is taught one shape at a time through object protocols.
Dataclasses, `TypedDict`s, mappings, sequences, plain classes and pydantic models are the ones that come with it.
Muotti is used by the [Bolinette project](https://github.com/bolinette) to build payloads, responses and configuration sections.

```python
from dataclasses import dataclass
from muotti import DataclassProtocol, Mapper


@dataclass
class UserPayload:
    name: str
    age: int


@dataclass
class User:
    name: str
    age: int
    active: bool = True


mapper = Mapper()
mapper.add_protocol(DataclassProtocol())

assert mapper.map(User, UserPayload("Bob", 42)) == User("Bob", 42, active=True)
```

## Installation

```shell
$ pip install muotti             # or use your preferred package manager
$ pip install muotti[pydantic]   # with the pydantic protocol and value converter
```

## Requirements

Muotti requires Python 3.13 (or newer).
It depends on [peritype](https://pypi.org/project/peritype/) to read types at runtime, on [mirino](https://pypi.org/project/mirino/) for the paths it names fields with, and on [escondite](https://pypi.org/project/escondite/) for the decorators.
[pydantic](https://pypi.org/project/pydantic/) is only needed for the `pydantic` extra.

## Object protocols

An `ObjectProtocol` teaches Muotti one kind of object: which fields a type has, how to read one from an instance, and how to build the type from the values collected for it.
A mapper starts empty, and resolves the first protocol that matches a type, in decreasing `priority`.

```python
from muotti import DataclassProtocol, Mapper, MappingProtocol

mapper = Mapper()
mapper.add_protocol(DataclassProtocol())
mapper.add_protocol(MappingProtocol())

assert mapper.map(dict[str, object], User, {"name": "Bob", "age": 42}).name == "Bob"
```

Some protocols know the fields of their type up front, such as the dataclass one.
Others are source-driven and take the keys they are given, which is how a `dict`, a `list` or a `set` destination is filled.

Writing a protocol is how a new kind of object is supported: subclass `ObjectProtocol`, implement `matches`, `fields`, `read_field` and `construct`, and give it a `priority` above the bundled ones if it must win over them.

## Mapping and merging

`map(dest_cls, src)` builds a new instance, and takes the source class from the source itself.
`map(src_cls, dest_cls, src)` is the form to use when the source cannot describe itself, a `dict` being the usual case.
`merge(src, dest)` maps into an instance that already exists, and leaves the fields absent from the source alone.

```python
user = mapper.map(User, UserPayload("Bob", 42))
mapper.merge(UserPayload("Alice", 30), user)

assert user == User("Alice", 30, active=True)
```

A field missing from the source is an error, unless the destination has a default for it or accepts `None`.
With `validate=True`, every field is tried and the failures are collected in a single `ValidationError` instead of raising on the first one.

```python
from muotti.errors import ValidationError

try:
    mapper.map(dict[str, object], User, {"name": "Bob"}, validate=True)
except ValidationError as err:
    assert [str(e.dest) for e in err.errors] == ["User.age"]
```

## Profiles

Field names do not always line up, and a `Profile` is where that is written down.
`register` opens a sequence between two types, and `for_attr` points at a destination field with a lambda.
That lambda receives an expression recorder, not an instance, so `lambda d: d.name` records the path to the field instead of reading anything.

```python
from muotti import Mapper, Profile


@dataclass
class Account:
    full_name: str


class AccountProfile(Profile):
    def __init__(self) -> None:
        super().__init__()
        self.register(Account, User).for_attr(
            lambda d: d.name,
            lambda opt: opt.map_from(lambda s: s.full_name),
        ).for_attr(
            lambda d: d.age,
            lambda opt: opt.default(lambda: 0),
        )


mapper.load_profiles([AccountProfile()])

assert mapper.map(User, Account("Bob")) == User("Bob", 0, active=True)
```

Besides `map_from` and `default`, an option can `ignore` a field, mark it `read_only`, `allow_write` on a field the protocol reports as immutable, or force a type with `use_type`.
A sequence can also run a function `before_mapping` or `after_mapping`, and `include` another pair of types to inherit its overrides.

`assert_configuration_valid` walks every loaded sequence and raises a `MappingConfigurationError` listing each destination field that is required, has no default, and has nothing in the source to fill it.
It is meant to run once at startup, rather than to discover the problem on the first request.

## Value conversion

Everything that is not a nested object ends up at a value converter, and that converter is a protocol:

```python
class ValueConverter(Protocol):
    def convert(self, value: Any, spec: FieldSpec, dest: ExpressionNode, src: ExpressionNode) -> Any: ...
```

A mapper uses `BasicValueConverter` unless it is given another one.
It passes a value through when it already has the target type, and otherwise applies a small set of conversions.
Numbers convert between themselves, `str` and `bytes` convert into each other, a string becomes a `UUID`, a `Decimal`, a `PurePath` or an `Enum` member, an ISO 8601 string becomes a `datetime`, a `date` or a `time`, and a number of seconds becomes a `timedelta`.
Lists, sets, tuples and dicts are converted element by element, and the members of a union are tried in order, the ones the value already matches first.
With `strict=True` nothing is converted, and only a value that already has the right type is accepted.

```python
from datetime import date
from muotti import BasicValueConverter, Mapper


@dataclass
class Event:
    day: date
    tags: set[str]


mapper = Mapper(BasicValueConverter())
mapper.add_protocol(DataclassProtocol())
mapper.add_protocol(MappingProtocol())

event = mapper.map(dict[str, object], Event, {"day": "2026-09-18", "tags": ["a", "a", "b"]})
assert event == Event(date(2026, 9, 18), {"a", "b"})
```

The `pydantic` extra brings a second converter, which hands the value to a `TypeAdapter` and follows pydantic's own rules.
It is the one to use when those rules are what the rest of an application already expects.

```python
from muotti import Mapper
from muotti.pydantic import PydanticProtocol, PydanticValueConverter

mapper = Mapper(PydanticValueConverter())
mapper.add_protocol(PydanticProtocol())
```

The two converters do not accept exactly the same values, so a mapper that used to run on pydantic should be given `PydanticValueConverter` explicitly rather than left on the default.

## Discovery through a cache

`mapping` and `mapping_protocol` record a profile or a protocol class in an [escondite](https://pypi.org/project/escondite/) cache when the module is imported, and `load_from_cache` instantiates everything the cache holds.
An application collects its profiles this way instead of importing each one by hand.

```python
from escondite import Cache
from muotti import Mapper, Profile, mapping

cache = Cache()


@mapping(cache=cache)
class AccountProfile(Profile): ...


mapper = Mapper()
mapper.load_from_cache(cache)
```

Both decorators take the bare and the parametrized form, and fall back to escondite's global cache when none is given.
Registering by hand with `add_protocol` and `load_profiles` works just as well, the cache is a convenience.

## Reference

### Bundled protocols

| Protocol                            | Destination                                   | Priority |
| ----------------------------------- | --------------------------------------------- | -------- |
| `PydanticProtocol`                  | `pydantic.BaseModel`, in `muotti.pydantic`    | 90       |
| `TypedDictProtocol`                 | `TypedDict`, with `NotRequired` and totality  | 80       |
| `DataclassProtocol`                 | `@dataclass` types                            | 70       |
| `MappingProtocol`                   | `dict` and other mappings, source-driven      | 20       |
| `SequenceProtocol` / `SetProtocol`  | `list`, `tuple` / `set`, `frozenset`          | 20       |
| `PlainObjectProtocol`               | Any class with annotated attributes           | 0        |

### Errors

All errors derive from `muotti.errors.MuottiError`, and a `MappingError` carries the `dest` and `src` paths the failure happened on.

| Error                        | Raised when                                                        |
| ---------------------------- | ------------------------------------------------------------------ |
| `SourceNotFoundError`        | A required destination field has nothing in the source             |
| `DestinationNotNullableError`| `None` reached a field that does not accept it                     |
| `ConversionError`            | A value could not be converted to the type of its field            |
| `InstantiationError`         | The destination type rejected the values collected for it          |
| `ImmutableFieldError`        | A field cannot be written to                                       |
| `NoProtocolError`            | No `ObjectProtocol` matched the destination type                   |
| `ValidationError`            | Raised with `validate=True`, and holds every error in `errors`     |
| `MappingConfigurationError`  | `assert_configuration_valid` found fields nothing can fill         |

## License

Muotti is released under the MIT license, see [LICENSE.txt](LICENSE.txt).
