"""Built-in decoders. Importing this package fills REGISTRY.

To add a format, create a module that calls `register(Decoder(...))` and
import it below.
"""

from .base import REGISTRY, Decoder, Option  # noqa: F401
from . import raw, tlv, protobuf, msgpack, cbor, bson, ubjson, bencode, thrift  # noqa: F401
