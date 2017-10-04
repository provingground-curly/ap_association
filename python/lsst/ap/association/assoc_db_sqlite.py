#
# LSST Data Management System
# Copyright 2017 LSST/AURA.
#
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the LSST License Statement and
# the GNU General Public License along with this program.  If not,
# see <http://www.lsstcorp.org/LegalNotices/>.
#

""" Simple sqlite3 interface for storing and retrieving DIAObjects and
DIASources. This class is mainly for testing purposes in the context of
ap_pipe/ap_verify.
"""

from __future__ import absolute_import, division, print_function

import sqlite3

from lsst.meas.algorithms.indexerRegistry import IndexerRegistry
import lsst.afw.table as afwTable
import lsst.afw.geom as afwGeom
import lsst.pex.config as pexConfig
import lsst.pipe.base as pipeBase
from .dia_collection import DIAObjectCollection
from .dia_object import \
    DIAObject, \
    make_minimal_dia_object_schema, \
    make_minimal_dia_source_schema

__all__ = ["AssociationDBSqliteConfig",
           "AssociationDBSqliteTask",
           "SqliteDBConverter"]


class SqliteDBConverter(object):
    """ Class for defining conversions to and from an sqlite database and
    afw SourceRecord objects.

    Attributes
    ----------
    schema : lsst.afw.table.Schema
        Schema defining the SourceRecord objects to be converted.
    table_name : str
        Name of the sqlite table this converter is to be used for.
    """

    def __init__(self, schema, table_name):
        self._schema = schema
        self._table_name = table_name
        self._afw_to_db_types = {
            "L": "INTEGER",
            "Angle": "REAL"
        }

    @property
    def table_name(self):
        """ Return name of the sqlite table this catalog is for
        """
        return self._table_name

    @property
    def schema(self):
        """ Return the internal catalog schema.

        Return
        ------
        lsst.afw.table.Schema
        """
        return self._schema

    def make_table_from_afw_schema(self, table_name):
        """ Convert the schema into a sqlite CREATE TABLE command.

        Parameters
        ----------
        table_name : str
            Name of the new table to create

        Return
        ------
        str
            A string of the query command to create the new table in sqlite.
        """
        name_type_string = ""
        for sub_schema in self._schema:
            tmp_name = sub_schema.getField().getName()
            tmp_type = self._afw_to_db_types[
                sub_schema.getField().getTypeString()]
            if tmp_name == 'id':
                tmp_type += " PRIMARY KEY"
            name_type_string += "%s %s," % (tmp_name, tmp_type)
        name_type_string = name_type_string[:-1]

        return "CREATE TABLE %s (%s)" % (table_name, name_type_string)

    def source_record_from_db_row(self, db_row):
        """ Create a source record from the values stored in a database row.

        Parameters
        ----------
        db_row : list of values
            Retrieved values from the database to convert into a SourceRecord.

        Return
        ------
        lsst.afw.table.SourceRecord
        """

        output_source_record = afwTable.SourceTable.makeRecord(
            afwTable.SourceTable.make(self._schema))

        for sub_schema, value in zip(self._schema, db_row):
            if sub_schema.getField().getTypeString() == 'Angle':
                output_source_record.set(
                    sub_schema.getKey(), value * afwGeom.degrees)
            else:
                output_source_record.set(
                    sub_schema.getKey(), value)
        return output_source_record

    def source_record_to_value_list(self, source_record):
        """ Convert a source record object into a list of its internal values.

        Parameters
        ----------
        source_record : afw.table.SourceRecord
            SourceRecord to convert

        Return
        ------
        list of values
        """
        values = []
        for sub_schema in self._schema:
            if sub_schema.getField().getTypeString() == 'Angle':
                values.append(
                    source_record.get(sub_schema.getKey()).asDegrees())
            else:
                values.append(source_record.get(sub_schema.getKey()))

        return values


class AssociationDBSqliteConfig(pexConfig.Config):
    """ Configuration parameters for the AssociationDBSqliteTask
    """
    db_name = pexConfig.Field(
        dtype=str,
        doc='Location on disk and name of the sqlite3 database for storing '
        'and loading DIASources and DIAObjects.',
        default=':memory:'
    )
    indexer = IndexerRegistry.makeField(
        doc='Select the spatial indexer to use within the database.',
        default='HTM'
    )


class AssociationDBSqliteTask(pipeBase.Task):
    """
    Enable storage of and reading of DIAObjects and DIASources from a
    sqlite database.

    Create a simple sqlite database and implement wrappers to store and
    retrieve DIAObjects and DIASources from within that database. This task
    functions as a testing ground for the L1 database and should mimic this
    database's eventual functionality. This specific database implementation is
    useful for the verification packages which may not be run with access to
    L1 database.

    Attributes
    -----------
    indexer : lsst.meas.algorithms.IndexerRegistry
        A spatial indexing object for fast look up of stored DIAObjects.
    """

    ConfigClass = AssociationDBSqliteConfig
    _DefaultName = "association_db_sqlite"

    def __init__(self, **kwargs):

        pipeBase.Task.__init__(self, **kwargs)
        self.indexer = IndexerRegistry[self.config.indexer.name](
            self.config.indexer.active)
        self._db_connection = sqlite3.connect(self.config.db_name)
        self._db_cursor = self._db_connection.cursor()

        self._dia_object_converter = SqliteDBConverter(
            make_minimal_dia_object_schema(), "dia_objects")
        self._dia_source_converter = SqliteDBConverter(
            make_minimal_dia_source_schema(), "dia_sources")

    def _commit(self):
        """ Save changes to the sqlite database.
        """
        self._db_connection.commit()

    def close(self):
        """ Close the connection to the sqlite database.
        """
        self._db_connection.close()

    def create_tables(self):
        """ If no sqlite database exists with the correct tables we can create
        one using this method.

        Returns
        -------
        bool
            Successfully created a new database with specified tables.
        """

        self._db_cursor.execute(
            'select name from sqlite_master where type = "table"')
        db_tables = self._db_cursor.fetchall()

        # If this database currently contains any tables exit and do not
        # create tables.
        if db_tables:
            return False
        else:
            # Create tables to store the individual DIAObjects and DIASources
            self._db_cursor.execute(
                self._dia_object_converter.make_table_from_afw_schema(
                    "dia_objects"))
            self._commit()
            self._db_cursor.execute(
                self._dia_source_converter.make_table_from_afw_schema(
                    "dia_sources"))
            self._commit()

            # Create linkage table between associated dia_objects and
            # dia_sources.
            self._db_cursor.execute(
                "CREATE TABLE dia_objects_to_dia_sources ("
                "src_id INTEGER PRIMARY KEY, "
                "obj_id INTEGER, "
                "FOREIGN KEY(src_id) REFERENCES dia_sources(id), "
                "FOREIGN KEY(obj_id) REFERENCES dia_objects(id)"
                ")")
            self._db_connection.commit()

        return True

    @pipeBase.timeMethod
    def load(self, ctr_coord, radius):
        """ Load all DIAObjects and associated DIASources within
        the specified circle.

        This method is approximate to the input circle as it will
        return the DIAObjects that are contained within the pixels
        produced by the indexer that cover the circle.

        Parameters
        ----------
        ctr_coord : lsst.afw.geom.SpherePoint
            Center position of the circle on the sky to load.
        radius : lsst.afw.geom.Angle
            Distance from ctr_coord defining a circle on the sky
            within which to load DIAObjects and associated DIASources.

        Returns
        -------
        lsst.ap.association.DIAObjectCollection
        """
        indexer_indices, on_boundry = self.indexer.get_pixel_ids(
            ctr_coord, radius)

        dia_objects = self._get_dia_objects(indexer_indices)

        dia_collection = DIAObjectCollection(dia_objects)

        return dia_collection

    @pipeBase.timeMethod
    def store(self, dia_collection, compute_spatial_index=False):
        """ Store all DIAObjects and DIASources in this dia_collection.

        This method should be used when adding a large number of DIAObjects
        and DIASources to their respective tables outside of the context of
        single visit processing.

        Parameters
        ----------
        dia_collection : lsst.ap.association.DIAObjectCollection
            Collection of DIAObjects to store. Also stores the DIASources
            associated with these DIAObjects.
        compute_spatial_index : bool
            If True, compute the spatial search indices using the
            indexer specified at class instantiation.
        """
        for dia_object in dia_collection.dia_objects:
            if compute_spatial_index:
                dia_object.dia_object_record.set(
                    'indexer_id', self.indexer.index_points(
                        [dia_object.ra.asDegrees()],
                        [dia_object.dec.asDegrees()])[0])
            self._store_record(
                dia_object.dia_object_record,
                self._dia_object_converter)
            for dia_source in dia_object.dia_source_catalog:
                self._store_record(
                    dia_source,
                    self._dia_source_converter)
                self._store_dia_object_source_pair(
                    dia_object.id, dia_source.getId())
        self._commit()

    @pipeBase.timeMethod
    def store_updated(self, dia_collection,
                      updated_dia_collection_ids):
        """ Store new DIAObjects and sources in the sqlite database.

        This method is intended to be used on a per-visit basis with the
        convention that one DIAObject will associated with one DIASource on a
        per visit and unassociated DIASources will result in a new DIAObject.

        Parameters
        ----------
        dia_collection : lsst.ap.association.DIAObjectCollection
            A collection of DIAObjects that contains newly created or updated
            DIAObjects. Only the new or updated DIAObjects are stored.
        updated_dia_collection_indices : int ndarray
            Ids of DIAObjects within the set DIAObjectCollection that should
            be stored as updated DIAObjects in the database.
        """
        for updated_collection_id in updated_dia_collection_ids:
            dia_object = dia_collection.get_dia_object(
                updated_collection_id)
            dia_object.dia_object_record.set(
                'indexer_id',
                self.indexer.index_points([dia_object.ra.asDegrees()],
                                          [dia_object.dec.asDegrees()])[0])
            self._store_record(
                dia_object.dia_object_record,
                self._dia_object_converter)
            self._store_record(
                dia_object.dia_source_catalog[-1],
                self._dia_source_converter)
            self._store_dia_object_source_pair(
                dia_object.id,
                dia_object.dia_source_catalog[-1].getId())
        self._commit()

    def _get_dia_objects(self, indexer_indices):
        """ Retrieve the DIAObjects from the database whose indexer indices
        are with the specified list of indices.

        Retrieves a list of DIAObjects that are covered by the pixels with
        indices, indexer_indices. Use this to retrieve the complete DIAObject
        including the associated sources.

        Parameters
        ----------
        indexer_indices : array like of ints
            Pixelized indexer indices from which to load.

        Returns
        -------
        list of lsst.ap.association.DIAObjects
        """
        output_dia_objects = []

        for row in self._query_dia_objects(indexer_indices):
            dia_object_record = \
                self._dia_object_converter.source_record_from_db_row(row)
            dia_sources = self._get_dia_sources(
                dia_object_record.getId())
            output_dia_objects.append(
                DIAObject(dia_sources, dia_object_record))

        return output_dia_objects

    def _query_dia_objects(self, indexer_indices):
        """ Query the database for the stored DIAObjects given a set of
        indices in the indexer.

        Parameters
        ----------
        indexer_indices : list of ints
            Spatial indices in the indexer specifying the area on the sky
            to load DIAObjects for.

        Return
        ------
        list of tuples
            Query result containing the values representing DIAObjects
        """
        self._db_cursor.execute(
            "CREATE TEMPORARY TABLE tmp_indexer_indices "
            "(indexer_id INTEGER PRIMARY KEY)")

        self._db_cursor.executemany(
            "INSERT OR REPLACE INTO tmp_indexer_indices VALUES (?)",
            [(int(indexer_index),) for indexer_index in indexer_indices])

        self._db_cursor.execute(
            "SELECT o.* FROM dia_objects AS o "
            "INNER JOIN tmp_indexer_indices AS i "
            "ON o.indexer_id = i.indexer_id")

        output_rows = self._db_cursor.fetchall()

        self._db_cursor.execute("DROP TABLE tmp_indexer_indices")
        self._commit()

        return output_rows

    def _get_dia_object_records(self, indexer_indices):
        """ Retrieve the SourceCatalog of objects representing the DIAObjects
        in the spatial indices specified.

        Retrieves the SourceRecords that are covered by the pixels with
        indices, indexer_indices. Use this to retrieve the summary statistics
        of the DIAObjects themselves rather than taking the extra overhead
        of loading the associated DIASources as well.

        Parameters
        ----------
        indexer_indices : list of ints
            Pixelized indexer indices from which to load.

        Returns
        -------
        a lsst.afw.table.SourceCatalog
        """
        output_dia_objects = afwTable.SourceCatalog(
            self._dia_object_converter.schema)

        for row in self._query_dia_objects(indexer_indices):
            dia_object_record = \
                self._dia_object_converter.source_record_from_db_row(row)
            output_dia_objects.append(dia_object_record)

        return output_dia_objects

    def _get_dia_sources(self, dia_obj_id):
        """ Retrieve all DIASources associated with this DIAObject id.

        Parameters
        ----------
        dia_obj_id : int
            Id of the DIAObject that is associated with the DIASources
            of interest.

        Returns
        -------
        lsst.afw.table.SourceCatalog
            SourceCatalog of DIASources
        """

        dia_source_schema = make_minimal_dia_source_schema()
        output_dia_sources = afwTable.SourceCatalog(dia_source_schema)

        self._db_cursor.execute(
            "SELECT s.* FROM dia_sources AS s "
            "INNER JOIN dia_objects_to_dia_sources AS a ON s.id = a.src_id "
            "WHERE a.obj_id = ?", (dia_obj_id,))

        rows = self._db_cursor.fetchall()
        output_dia_sources.reserve(len(rows))

        for row in rows:
            output_dia_sources.append(
                self._dia_source_converter.source_record_from_db_row(row))

        return output_dia_sources

    def _store_dia_object_source_pair(self, obj_id, src_id):
        """ Store a link between a DIAObject id and a DIASource.

        Parameters
        ----------
        obj_id : int
            Id of DIAObject
        src_id : int
            Id of DIASource
        """
        self._db_cursor.execute(
            "INSERT OR REPLACE INTO dia_objects_to_dia_sources "
            "VALUES (?, ?)", (src_id, obj_id))

    def _store_record(self, source_record, converter):
        """ Store an individual SourceRecord into the database.

        Parameters
        ----------
        source_record : lsst.afw.table.SourcRecord
            SourceRecord to store in the database table specified by
            converter.
        converter : lsst.ap.association.SqliteDBConverter
            A converter object specifying the correct database table to write
            into.
        """
        values = converter.source_record_to_value_list(source_record)
        insert_string = ("?," * len(values))[:-1]

        self._db_cursor.execute(
            "INSERT OR REPLACE INTO %s VALUES (%s)" %
            (converter.table_name, insert_string), values)