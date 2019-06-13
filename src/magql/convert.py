"""
GQLMagic converts a dictionary of SQLAlchemy tables into a generic GraphQL
Schema. Each table has 3 autogenerated mutations, create, update, and delete,
and 2 autogenerated queries, single and many. Each mutation has a marshmallow
schema automatically generated for it based on the types of its fields.
The validations can be overridden with decorators, as explained later.
"""
from magql.validator import get_validator_overrides
from graphql import GraphQLSchema, GraphQLField, GraphQLObjectType, GraphQLList, GraphQLNonNull, \
    GraphQLString, GraphQLInputField, GraphQLArgument, GraphQLID, GraphQLInputObjectType, GraphQLEnumType, GraphQLScalarType
from marshmallow_sqlalchemy import ModelSchema
from sqlalchemy_utils import get_mapper
from magql.get_type import get_type, get_required_type, get_filter_type
from magql.resolver_factory import Resolver, ManyResolver, SingleResolver, CreateResolver, UpdateResolver, DeleteResolver, EnumResolver
from magql.filter import RelFilter
from magql.join import join_schemas
from inflection import pluralize, camelize
from collections import namedtuple


def js_camelize(word):
    # add config check
    return camelize(word, False)


def get_tables(db):
    try:
        return db.metadata.tables
    except AttributeError:
        print("Could not find database tables")


GraphQLObjectTypes = namedtuple('GQLObjectTypes', ['object', 'filter', 'sort', 'input', 'required_input', 'payload', 'schema'])


def is_rel_required(rel):
    fk = rel._user_defined_foreign_keys.union(rel._calculated_foreign_keys).pop()
    return not fk.nullable


class MagqlSchema:
    """
     Creates a subclass of GraphQLSchema which has been extended to
     provide the capability to override resolvers. Takes in SQLAlchemy
     tables, taken from the metadata of a SQLAlchemy declarative base.

    :param tables: A dict with values of each table to generate a Schema
            for and keys of the tablename
    """
    def __init__(self, tables, existing_schema=None):
        query_fields = {}
        mutation_fields = {}
        self.table_types = {}

        # make query columns and mutations
        for table_name, table in tables.items():
            try:
                get_mapper(table)
            except ValueError:
                print(f"No Mapper for table {table.name}")
                continue
            # Build only columns first so GQLObjectTypes are built for all tables
            self.generate_column_object_types(table_name, table)

            # Then add resolvers
            mutation_fields = {**mutation_fields, **self.generate_mutations(table_name, table)}

            query_fields = {**query_fields, **self.generate_column_queries(table_name, table)}

        for table_name, table in tables.items():
            try:
                table_mapper = get_mapper(table)
            except ValueError:
                print(f"No Mapper for table {table.name}")
                continue

            for relationship_name, rel in table_mapper.relationships.items():
                direction = rel.direction.name
                required = is_rel_required(rel)

                object = self.table_types[table].object
                input = self.table_types[table].input
                required_input = self.table_types[table].required_input
                filter_ = self.table_types[table].filter

                # rel_object is used for queries so it must be recursive
                rel_object = self.table_types[rel.target].object

                # inputs are for mutations so should not be recursive
                rel_input = GraphQLID
                rel_required_input = GraphQLID

                if 'TOMANY' in direction:
                    rel_object = GraphQLList(rel_object)
                    rel_input = GraphQLList(rel_input)
                    rel_required_input = GraphQLList(rel_required_input)
                # 'TOMANY' cannot be required
                elif required:
                    rel_required_input = GraphQLNonNull(rel_required_input)

                relationship_name = js_camelize(relationship_name)

                required_input.fields[relationship_name] = GraphQLInputField(rel_required_input)
                input.fields[relationship_name] = GraphQLInputField(rel_input)
                object.fields[relationship_name] = GraphQLField(rel_object, None, Resolver())
                filter_.fields[relationship_name] = GraphQLInputField(RelFilter)

        query = GraphQLObjectType("Query", query_fields)
        mutation = GraphQLObjectType("Mutation", mutation_fields)

        self.schema = GraphQLSchema(query, mutation)

        if existing_schema is not None:
            self.merge_schema(existing_schema)


    def generate_column_object_types(self, table_name, table):
        fields, filter_fields, sort_fields, required_input_fields, input_fields = self.build_fields_from_column(table)

        try:
            table_class = get_mapper(table).class_
        except ValueError:
            print(table)
            return

        schema_overrides = get_validator_overrides(table_class)
        schema_overrides["Meta"] = type("Meta", (object,), {
            "model": table_class,
        })

        camelized = camelize(table.name)
        gql_object = GraphQLObjectType(camelized, fields)
        self.table_types[table] = GraphQLObjectTypes(
            object=gql_object,
            filter=GraphQLInputObjectType(camelized + "Filter", filter_fields),
            sort=GraphQLEnumType(camelized + 'Sort', sort_fields),
            input=GraphQLInputObjectType(camelized + "Input", input_fields),
            required_input=GraphQLInputObjectType(camelized + "InputRequired", required_input_fields),
            payload=GraphQLNonNull(GraphQLObjectType(camelized + "Payload", {
                'error': GraphQLList(GraphQLString),
                table_name: gql_object
            })),
            schema=type(camelized + "Schema", (ModelSchema,), schema_overrides)
        )


    def build_fields_from_column(self, table):
        fields = {}
        required_input_fields = {}
        input_fields = {}
        filter_fields = {}
        sort_fields = {}
        for column_name, column in table.c.items():
            if column.foreign_keys:
                pass
            else:
                column_name = js_camelize(column_name)

                base_type = get_type(column)
                fields[column_name] = GraphQLField(base_type)

                # TODO: Refactor how enums are handled
                if isinstance(base_type, GraphQLEnumType):
                    fields[column_name].resolve = EnumResolver()
                required_input_fields[column_name] = GraphQLInputField(get_required_type(column, base_type))
                input_fields[column_name] = GraphQLInputField(base_type)
                filter_fields[column_name] = get_filter_type(column, base_type)
                sort_fields[column_name + "_asc"] = column_name + "_asc",
                sort_fields[column_name + "_desc"] = column_name + "_desc",

        return fields, filter_fields, sort_fields, required_input_fields, input_fields

    def generate_mutations(self, table_name, table):

        required_input = self.table_types[table].required_input
        input = self.table_types[table].input
        payload = self.table_types[table].payload
        schema = self.table_types[table].schema
        fields = {}

        id_arg = GraphQLArgument(GraphQLNonNull(GraphQLID))
        input_arg = GraphQLArgument(GraphQLNonNull(input))
        required_input_arg = GraphQLArgument(GraphQLNonNull(required_input))

        create_args = {
            "input": required_input_arg
        }

        update_args = {
            "id": id_arg,
            "input": input_arg
        }

        delete_args = {
            "id": id_arg,
        }
        camelized = js_camelize(table_name)
        fields["create" + camelized] = GraphQLField(payload, create_args, CreateResolver(table, schema))
        fields["update" + camelized] = GraphQLField(payload, update_args, UpdateResolver(table, schema))
        fields["delete" + camelized] = GraphQLField(payload, delete_args, DeleteResolver(table, schema))

        return fields

    def generate_column_queries(self, table_name, table):

        table_gql_object = self.table_types[table].object
        filter_obj = self.table_types[table].filter
        sort_obj = self.table_types[table].sort
        fields = {
            js_camelize(table_name): GraphQLField(
                table_gql_object,
                {"id": GraphQLArgument(GraphQLNonNull(GraphQLID))},
                SingleResolver(table)
            ),
            js_camelize(pluralize(table_name)): GraphQLField(
                GraphQLList(table_gql_object),
                {
                    "filter": GraphQLArgument(filter_obj),
                    "sort": GraphQLArgument(GraphQLList(GraphQLNonNull(sort_obj)))
                },
                ManyResolver(table)
            )
        }
        return fields

    @staticmethod
    def join_types(type1, type2):
        for field_name, field in type2.fields.items():
            if field_name in type1.fields:
                raise Exception("Duplicate fields in type, cannot resolve")
        new_fields = {**type1.fields, **type2.fields}

        return GraphQLObjectType(type1.name, new_fields)

    @staticmethod
    def get_merged_object_type(return_type, joined_type_map):
        if isinstance(return_type, GraphQLList):
            return GraphQLList(MagqlSchema.get_merged_object_type(return_type.of_type, joined_type_map))

        if isinstance(return_type, GraphQLNonNull):
            return GraphQLNonNull(MagqlSchema.get_merged_object_type(return_type.of_type, joined_type_map))

        type_name = return_type.name

        if type_name in joined_type_map:
            return_type = joined_type_map[type_name]

        return return_type

    @staticmethod
    def generate_new_return_type(return_type, joined_type_map):

        if isinstance(return_type, GraphQLList):
            return GraphQLList(MagqlSchema.generate_new_return_type(return_type.of_type, joined_type_map))

        if isinstance(return_type, GraphQLNonNull):
            return GraphQLNonNull(MagqlSchema.generate_new_return_type(return_type.of_type, joined_type_map))

        type_name = return_type.name
        if type_name in joined_type_map:
            return_type = joined_type_map[type_name]

        new_sub_fields = {}
        for field_name, field in return_type.fields.items():
            new_sub_fields[field_name] = field
            if MagqlSchema.get_object_type(field.type).name in joined_type_map:
                new_sub_fields[field_name].type = MagqlSchema.get_merged_object_type(field.type, joined_type_map)
        return_type.fields = new_sub_fields
        return return_type

    @staticmethod
    def get_object_type(field):
        while not getattr(field, "name", None):
            field = field.of_type
        return field

    @staticmethod
    def generate_new_field(field, joined_type_map):
        type_ = MagqlSchema.generate_new_return_type(field.type, joined_type_map)

        return GraphQLField(
            type_,
            field.args,
            field.resolve,
            field.subscribe,
            field.description,
            field.deprecation_reason
        )

    def merge_schema(self, schema):
        assert isinstance(schema, GraphQLSchema)

        joined_type_map = {}
        for type_name, type in schema.type_map.items():
            if type_name in self.schema.type_map and not isinstance(type, GraphQLScalarType) and not type_name.startswith("__"):
                joined_type_map[type_name] = (MagqlSchema.join_types(self.schema.type_map[type_name], type))

        new_query_fields = {}
        new_mutation_fields = {}

        for query_name, field in getattr(self.schema.query_type, "fields",{}).items():
            new_query_fields[query_name] = MagqlSchema.generate_new_field(field, joined_type_map)

        for query_name, field in getattr(schema.query_type, "fields", {}).items():
            new_query_fields[query_name] = MagqlSchema.generate_new_field(field, joined_type_map)

        for mutation_name, field in getattr(self.schema.mutation_type, "fields", {}).items():
            new_mutation_fields[mutation_name] = MagqlSchema.generate_new_field(field, joined_type_map)

        for mutation_name, field in getattr(schema.mutation_type, "fields", {}).items():
            new_mutation_fields[mutation_name] = MagqlSchema.generate_new_field(field, joined_type_map)

        new_query = GraphQLObjectType("Query", new_query_fields)
        new_mutation = GraphQLObjectType("Mutation", new_mutation_fields)
        self.schema = GraphQLSchema(new_query, new_mutation)
    """
    Subclass of GraphQLSchema that allows the overriding of
    """
    def override_resolver(self, field_name, resolver):
        if field_name in self.schema.mutation_type.fields:
            self.schema.mutation_type.fields[field_name].resolve = resolver
        elif field_name in self.schema.query_type.fields:
            self.schema.query_type.fields[field_name].resolve = resolver
