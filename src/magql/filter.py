from functools import singledispatch

from inflection import underscore
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import Text
from sqlalchemy import Time
from sqlalchemy import Unicode
from sqlalchemy import UnicodeText
from sqlalchemy.orm import RelationshipProperty
from sqlalchemy.types import Boolean
from sqlalchemy.types import DECIMAL
from sqlalchemy.types import FLOAT
from sqlalchemy.types import Integer
from sqlalchemy.types import String
from sqlalchemy.types import VARCHAR
from sqlalchemy_utils import EmailType
from sqlalchemy_utils import get_mapper
from sqlalchemy_utils import JSONType
from sqlalchemy_utils import PhoneNumberType
from sqlalchemy_utils import URLType
from sqlalchemy_utils.types import ChoiceType

from magql.definitions import MagqlEnumType
from magql.definitions import MagqlInputField
from magql.definitions import MagqlInputObjectType
from magql.logging import magql_logger

NOT_FOUND = "filter operator not found"

StringFilter = MagqlInputObjectType(
    "StringFilter",
    {
        "operator": MagqlInputField(
            MagqlEnumType(
                "StringOperator", {"INCLUDES": "INCLUDES", "EQUALS": "EQUALS"}
            )
        ),
        "value": MagqlInputField("String"),
    },
)


DateFilter = MagqlInputObjectType(
    "DateFilter",
    {
        "operator": MagqlInputField(
            MagqlEnumType(
                "DateOperator", {"BEFORE": "BEFORE", "ON": "ON", "AFTER": "AFTER"}
            )
        ),
        "value": MagqlInputField("String"),
    },
)


IntFilter = MagqlInputObjectType(
    "IntFilter",
    {
        "operator": MagqlInputField(
            MagqlEnumType(
                "IntOperator",
                {
                    "lt": "lt",
                    "lte": "lte",
                    "eq": "eq",
                    "neq": "neq",
                    "gt": "gt",
                    "gte": "gte",
                },
            )
        ),
        "value": MagqlInputField("Int"),
    },
)

FloatFilter = MagqlInputObjectType(
    "FloatFilter",
    {
        "operator": MagqlInputField(
            MagqlEnumType(
                "FloatOperator",
                {
                    "lt": "lt",
                    "lte": "lte",
                    "eq": "eq",
                    "neq": "neq",
                    "gt": "gt",
                    "gte": "gte",
                },
            )
        ),
        "value": MagqlInputField("Float"),
    },
)

RelFilter = MagqlInputObjectType(
    "RelFilter",
    {
        "operator": MagqlInputField(
            MagqlEnumType("RelOperator", {"INCLUDES": "INCLUDES"})
        ),
        "value": MagqlInputField("Int"),
    },
)


BooleanFilter = MagqlInputObjectType(
    "BooleanFilter",
    {
        "operator": MagqlInputField(
            MagqlEnumType(
                "BooleanOperator", {"EQUALS": "EQUALS", "NOTEQUALS": "NOTEQUALS"}
            )
        ),
        "value": MagqlInputField("Boolean"),
    },
)

EnumOperator = MagqlEnumType("EnumOperator", {"INCLUDES": "INCLUDES"})


def EnumFilter(base_type):
    name = base_type.name + "Filter"

    input_ = {
        "operator": MagqlInputField(EnumOperator),
        "value": MagqlInputField(base_type),
    }
    return MagqlInputObjectType(name, input_)


@singledispatch
def get_filter_comparator(_):
    magql_logger.error(NOT_FOUND)


@get_filter_comparator.register(RelationshipProperty)
def _(rel):
    direction = rel.direction.name
    if "TOONE" in direction:

        def condition(filter_value, filter_operator, field):
            if filter_operator == "INCLUDES":
                return field == filter_value
            else:
                magql_logger.error("filter operator not found")

        return condition
    elif "TOMANY" in direction:

        def condition(filter_value, filter_operator, field):
            if filter_operator == "INCLUDES":
                return field.any(field.contains(filter_value))

        return condition
    # Raise error


@get_filter_comparator.register(DateTime)
@get_filter_comparator.register(Date)
def _(_):
    def condition(filter_value, filter_operator, field):
        if filter_operator == "BEFORE":
            return field < filter_value
        elif filter_operator == "ON":
            return field == filter_value
        elif filter_operator == "After":
            return field > filter_value

    return condition


@get_filter_comparator.register(JSONType)
@get_filter_comparator.register(Text)
@get_filter_comparator.register(UnicodeText)
@get_filter_comparator.register(Unicode)
@get_filter_comparator.register(URLType)
@get_filter_comparator.register(PhoneNumberType)
@get_filter_comparator.register(EmailType)
@get_filter_comparator.register(Time)
@get_filter_comparator.register(String)
@get_filter_comparator.register(VARCHAR)
def _(_):
    def condition(filter_value, filter_operator, field):
        if filter_operator == "INCLUDES":
            return field.like(f"%{filter_value}%")
        elif filter_operator == "EQUALS":
            return field == filter_value
        else:
            magql_logger.error(NOT_FOUND)

    return condition


@get_filter_comparator.register(FLOAT)
@get_filter_comparator.register(DECIMAL)
@get_filter_comparator.register(Integer)
def _(_):
    def condition(filter_value, filter_operator, field):
        if filter_operator == "lt":
            return field < filter_value
        elif filter_operator == "lte":
            return field <= filter_value
        elif filter_operator == "eq":
            return field == filter_value
        elif filter_operator == "neq":
            return field != filter_value
        elif filter_operator == "gt":
            return field > filter_value
        elif filter_operator == "gte":
            return field >= filter_value
        else:
            magql_logger.error(NOT_FOUND)

    return condition


@get_filter_comparator.register(Boolean)
def _(_):
    def condition(filter_value, filter_operator, field):
        if filter_operator == "EQUALS":
            return field == filter_value
        elif filter_operator == "NOTEQUALS":
            return field != filter_value
        else:
            magql_logger.error(NOT_FOUND)

    return condition


@get_filter_comparator.register(ChoiceType)
def _(_):
    def condition(filter_value, filter_operator, field):
        if filter_operator == "INCLUDES":
            return field == filter_value
        else:
            magql_logger.error(NOT_FOUND)

    return condition


def generate_filters(table, info, *args, **kwargs):
    sqla_filters = []
    if "filter" in kwargs and kwargs["filter"] is not None:
        mapper = get_mapper(table)
        gql_filters = kwargs["filter"]
        for filter_name, gql_filter in gql_filters.items():
            gql_filter_value = gql_filter["value"]
            filter_name = underscore(filter_name)
            if filter_name in table.c:
                filter_type = table.c[filter_name].type
            elif filter_name in mapper.relationships:
                rel = mapper.relationships[filter_name]
                rel_mapper = get_mapper(rel.target)
                gql_filter_value = (
                    info.context.query(rel_mapper.class_)
                    .filter_by(id=gql_filter_value)
                    .one()
                )
                filter_type = rel
            else:
                magql_logger.error("Unknown field on sqlamodel")

            sql_filter = get_filter_comparator(filter_type)(
                gql_filter_value,
                gql_filter["operator"],
                getattr(mapper.class_, filter_name),
            )
            sqla_filters.append(sql_filter)
    return sqla_filters
