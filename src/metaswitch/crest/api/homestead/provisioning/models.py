# @file models.py
#
# Project Clearwater - IMS in the Cloud
# Copyright (C) 2013  Metaswitch Networks Ltd
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation, either version 3 of the License, or (at your
# option) any later version, along with the "Special Exception" for use of
# the program along with SSL, set forth below. This program is distributed
# in the hope that it will be useful, but WITHOUT ANY WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details. You should have received a copy of the GNU General Public
# License along with this program.  If not, see
# <http://www.gnu.org/licenses/>.
#
# The author can be reached by email at clearwater@metaswitch.com or by
# post at Metaswitch Networks Ltd, 100 Church St, Enfield EN2 6BQ, UK
#
# Special Exception
# Metaswitch Networks Ltd  grants you permission to copy, modify,
# propagate, and distribute a work formed by combining OpenSSL with The
# Software, or a work derivative of such a combination, even if such
# copying, modification, propagation, or distribution would otherwise
# violate the terms of the GPL. You must comply with the GPL in all
# respects for all of the code used other than OpenSSL.
# "OpenSSL" means OpenSSL toolkit software distributed by the OpenSSL
# Project and licensed under the OpenSSL Licenses, or a work based on such
# software and licensed under the OpenSSL Licenses.
# "OpenSSL Licenses" means the OpenSSL License and Original SSLeay License
# under which the OpenSSL Project distributes the OpenSSL toolkit software,
# as those licenses appear in the file LICENSE-OPENSSL.

import xml.etree.ElementTree as ET
import StringIO
import uuid
import logging

from twisted.internet import defer
from telephus.cassandra.ttypes import NotFoundException

from .. import config
from ..auth_vectors import DigestAuthVector
from metaswitch.crest.api import utils
from ..cassandra import CassandraModel

_log = logging.getLogger("crest.api.homestead.provisioning")

NULL_COLUMN_VALUE = ""


def convert_uuid(this_uuid):
    """Convert a uuid in various formats to a byte array."""

    if isinstance(this_uuid, uuid.UUID):
        # UUID has been passed as a UUID object.  Convert it to bytes.
        return this_uuid.bytes
    elif isinstance(this_uuid, basestring):
        # UUID has been passed as a string.  It could either be a byte
        # array, or a string of the form 123456-1234-1234-1234-1234567890ab
        try:
            return uuid.UUID(this_uuid).bytes
        except ValueError:
            pass

        try:
            return uuid.UUID(bytes=this_uuid).bytes
        except ValueError:
            pass

    # Not got a valid UUID.
    raise ValueError("Row key must be a UUID or a byte array " +
                     "(got %s of type %s)" % (this_uuid, type(this_uuid)))


def uuid_to_str(this_uuid):

    if isinstance(this_uuid, basestring) and (len(this_uuid) == 16):
        # Got a 16 length string, so this is probably the uuid as a byte stream.
        return str(uuid.UUID(bytes=this_uuid))
    else:
        # Assume the UUID is already formatted correctly, or is a type that can
        # be converted to str sensibly.
        return str(this_uuid)


class ProvisioningModel(CassandraModel):
    cass_keyspace = config.PROVISIONING_KEYSPACE

    @classmethod
    def register_cache(cls, cache):
        """Register the cache object so provisioning models can update it with
        the result of provisioning operations"""
        cls._cache = cache

    @defer.inlineCallbacks
    def assert_row_exists(self):
        """Checks if the row exists and if not raise NotFoundException"""

        # To check if the row exists, simply try to read all it's columns.
        try:
            yield self.get_columns()
        except:
            _log.debug("Row %s:%s does not exist" %
                       (self.cass_table, self.row_key_str))
            raise


class IRS(ProvisioningModel):
    """Model representing an implicit registration set"""

    ASSOC_PRIVATE_PREFIX = "associated_private_"
    SERVICE_PROFILE_PREFIX = "service_profile_"

    cass_table = config.IRS_TABLE

    def __init__(self, row_key):
        super(IRS, self).__init__(convert_uuid(row_key))

        # The row key is stored a byte array so need to explicitly store a human
        # readable version.
        self.row_key_str = uuid_to_str(row_key)

    @classmethod
    @defer.inlineCallbacks
    def create(cls):
        irs_uuid = uuid.uuid4()
        _log.debug("Create IRS %s" % irs_uuid)

        yield IRS(irs_uuid).touch()
        defer.returnValue(irs_uuid)

    @defer.inlineCallbacks
    def get_associated_privates(self):
        priv_hash = yield self.get_columns_with_prefix_stripped(self.ASSOC_PRIVATE_PREFIX)
        defer.returnValue(priv_hash.keys())

    @defer.inlineCallbacks
    def get_associated_service_profiles(self):
        sp_hash = yield self.get_columns_with_prefix_stripped(self.SERVICE_PROFILE_PREFIX)
        defer.returnValue(sp_hash.keys())

    @defer.inlineCallbacks
    def get_associated_publics(self):
        sp_uuids = yield self.get_associated_service_profiles()

        public_ids = utils.flatten(
                                [(yield ServiceProfile(uuid).get_public_ids())
                                 for uuid in sp_uuids])
        defer.returnValue(public_ids)

    @defer.inlineCallbacks
    def associate_private_id(self, private_id):
        yield self.assert_row_exists()
        yield self.modify_columns({self.ASSOC_PRIVATE_PREFIX + private_id:
                                                            NULL_COLUMN_VALUE})

    @defer.inlineCallbacks
    def dissociate_private_id(self, private_id):
        yield self.delete_column(self.ASSOC_PRIVATE_PREFIX + private_id)

    @defer.inlineCallbacks
    def associate_service_profile(self, sp_uuid):
        yield self.assert_row_exists()
        yield self.modify_columns({self.SERVICE_PROFILE_PREFIX + str(sp_uuid):
                                                            NULL_COLUMN_VALUE})
        yield self.rebuild()

    @defer.inlineCallbacks
    def dissociate_service_profile(self, sp_uuid):
        yield self.delete_column(self.SERVICE_PROFILE_PREFIX + str(sp_uuid))
        yield self.rebuild()

    @defer.inlineCallbacks
    def delete(self):
        _log.debug("Delete IRS %s" % self.row_key_str)

        sp_uuids = yield self.get_associated_service_profiles()
        for uuid in sp_uuids:
            yield ServiceProfile(uuid).delete()

        private_ids = yield self.get_associated_privates()
        for priv in private_ids:
            yield PrivateID(priv).dissociate_irs(self.row_key)

        self.delete_row()

    @defer.inlineCallbacks
    def build_imssubscription_xml(self):
        """Create an IMS subscription document for this IRS"""

        # Create an IMS subscription mode with a dummy private ID node.
        root = ET.Element("IMSSubscription")
        root.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
        root.set("xsi:noNamespaceSchemaLocation", "CxDataType.xsd")

        priv_elem = ET.SubElement(root, "PrivateID")
        priv_elem.text = "Unspecified"

        sp_uuids = yield self.get_associated_service_profiles()
        for sp_uuid in sp_uuids:
            # Add a ServiceProfile node for each profile in this IRS with iFCs.
            # (ignore it entirely if it doesn't have any iFCs).
            #
            # Note that the IFC XML contains a wrapping <ServiceProfile> tag.
            try:
                ifc_xml = yield ServiceProfile(sp_uuid).get_ifc()
                sp_elem = ET.fromstring(ifc_xml)

                # Add a PublicIdentity node for each ID in this service
                # profile. The contents of this node are stored in the
                # database.
                public_ids = yield ServiceProfile(sp_uuid).get_public_ids()
                for pub_id in public_ids:
                    pub_id_xml = yield PublicID(pub_id).get_publicidentity()
                    pub_id_xml_elem = ET.fromstring(pub_id_xml)
                    sp_elem.append(pub_id_xml_elem)

                # Append the Service Profile to the IMS subscription.
                root.append(sp_elem)

            except NotFoundException:
                pass


        # Generate the new IMS subscription XML document.
        output = StringIO.StringIO()
        tree = ET.ElementTree(root)
        tree.write(output, encoding="UTF-8", xml_declaration=True)
        xml = output.getvalue()

        defer.returnValue(xml)

    @defer.inlineCallbacks
    def rebuild(self):
        """
        Rebuild the IMPI and IMPU tables in the cache when the IRS (or it's
        children are modified).
        """
        _log.debug("Rebuild cache for IRS %s" % self.row_key_str)

        xml = yield self.build_imssubscription_xml()
        timestamp = self._cache.generate_timestamp()

        for pub_id in (yield self.get_associated_publics()):
            yield self._cache.put_ims_subscription(pub_id, xml, timestamp)

        for priv_id in (yield self.get_associated_privates()):
            yield PrivateID(priv_id).rebuild()


class PrivateID(ProvisioningModel):
    """Model representing a provisioned private ID"""

    DIGEST_HA1 = "digest_ha1"
    REALM = "realm"
    ASSOC_IRS_PREFIX = "associated_irs_"

    cass_table = config.PRIVATE_TABLE

    @defer.inlineCallbacks
    def get_digest(self):
        columns = yield self.get_columns([self.DIGEST_HA1, self.REALM])
        defer.returnValue((columns[self.DIGEST_HA1],
                           columns.get(self.REALM)))

    @defer.inlineCallbacks
    def get_irses(self):
        irses_hash = \
            yield self.get_columns_with_prefix_stripped(self.ASSOC_IRS_PREFIX)
        defer.returnValue(irses_hash.keys())

    @defer.inlineCallbacks
    def get_public_ids(self):
        irs_uuids = yield self.get_irses()
        public_ids = utils.flatten([(yield IRS(uuid).get_associated_publics())
                                                         for uuid in irs_uuids])
        defer.returnValue(public_ids)

    @defer.inlineCallbacks
    def put_digest(self, digest, realm=None):
        _log.debug("Create private ID %s" % self.row_key_str)

        columns = {self.DIGEST_HA1: digest}
        if realm:
            columns[self.REALM] = realm
        yield self.modify_columns(columns)
        yield self._cache.put_av(self.row_key,
                                 DigestAuthVector(digest, realm, None, True),
                                 self._cache.generate_timestamp())

    @defer.inlineCallbacks
    def delete(self):
        _log.debug("Delete private ID %s" % self.row_key_str)

        irs_uuids = yield self.get_irses()
        for uuid in irs_uuids:
            yield IRS(uuid).dissociate_private_id(self.row_key)

        yield self.delete_row()
        yield self._cache.delete_private_id(self.row_key,
                                            self._cache.generate_timestamp())

    @defer.inlineCallbacks
    def associate_irs(self, irs_uuid):
        yield self.assert_row_exists()
        yield self.modify_columns({self.ASSOC_IRS_PREFIX + uuid_to_str(irs_uuid):
                                                            NULL_COLUMN_VALUE})
        yield IRS(irs_uuid).associate_private_id(self.row_key)
        yield self.rebuild()

    @defer.inlineCallbacks
    def dissociate_irs(self, irs_uuid):
        yield self.delete_column(self.ASSOC_IRS_PREFIX + uuid_to_str(irs_uuid))
        yield IRS(irs_uuid).dissociate_private_id(self.row_key)
        yield self.rebuild()

    @defer.inlineCallbacks
    def rebuild(self):
        """ Rebuild the IMPI table in the cache """
        _log.debug("Rebuild cache for private ID %s" % self.row_key_str)

        # Get all the information we need to rebuild the cache.  Do this before
        # deleting any cache entries to minimize the time the cache is empty.
        digest = yield self.get_digest()

        public_ids = []
        for irs in (yield self.get_irses()):
            for pub_id in (yield IRS(irs).get_associated_publics()):
                public_ids.append(pub_id)

        timestamp = self._cache.generate_timestamp()

        # Delete the existing cache entry then write back the digest and the
        # associated public IDs.  Take 1ms off the timestamp to ensure the
        # update happens after the delete.
        yield self._cache.delete_private_id(self.row_key, timestamp - 1000)
        yield self._cache.put_av(self.row_key,
                                 DigestAuthVector(digest, None, None, True),
                                 self._cache.generate_timestamp())
        for pub_id in public_ids:
            yield self._cache.put_associated_public_id(self.row_key,
                                                       pub_id,
                                                       timestamp)


class PublicID(ProvisioningModel):
    """Model representing a provisioned public identity"""

    SERVICE_PROFILE = "service_profile"
    PUBLICIDENTITY = "publicidentity"

    cass_table = config.PUBLIC_TABLE

    @defer.inlineCallbacks
    def get_sp(self):
        sp_uuid = yield self.get_column_value(self.SERVICE_PROFILE)
        defer.returnValue(sp_uuid)

    @defer.inlineCallbacks
    def get_publicidentity(self):
        _log.debug("Create public ID %s" % self.row_key_str)
        xml = yield self.get_column_value(self.PUBLICIDENTITY)
        defer.returnValue(xml)

    @defer.inlineCallbacks
    def get_irs(self):
        sp_uuid = yield self.get_sp()
        irs_uuid = yield ServiceProfile(sp_uuid).get_irs()
        defer.returnValue(irs_uuid)

    @defer.inlineCallbacks
    def get_private_ids(self):
        irs_uuid = yield self.get_irs()
        private_ids = yield IRS(irs_uuid).get_associated_privates()
        defer.returnValue(private_ids)

    @defer.inlineCallbacks
    def put_publicidentity(self, xml, sp_uuid):
        yield self.modify_columns({self.PUBLICIDENTITY: xml,
                                   self.SERVICE_PROFILE: sp_uuid})

    @defer.inlineCallbacks
    def delete(self):
        _log.debug("Delete public ID %s" % self.row_key_str)
        irs_uuid = yield self.get_irs()
        sp_uuid = yield self.get_sp()

        yield ServiceProfile(sp_uuid).dissociate_public_id(self.row_key)
        yield self.delete_row()
        yield self._cache.delete_public_id(self.row_key,
                                           self._cache.generate_timestamp())

        yield IRS(irs_uuid).rebuild()


class ServiceProfile(ProvisioningModel):
    """Model representing a provisioned service profile"""

    IRS_COLUMN = "irs"
    IFC_COLUMN = "initialfiltercriteria"
    PUBLIC_ID_COLUMN_PREFIX = "public_id_"

    cass_table = config.SP_TABLE

    def __init__(self, row_key):
        super(ServiceProfile, self).__init__(convert_uuid(row_key))

        # The row key is stored a byte array so need to explicitly store a human
        # readable version.
        self.row_key_str = uuid_to_str(row_key)

    @classmethod
    @defer.inlineCallbacks
    def create(self, irs_uuid):
        sp_uuid = uuid.uuid4()
        _log.debug("Create service profile %s" % sp_uuid)

        yield ServiceProfile(sp_uuid).modify_columns(
                                            {self.IRS_COLUMN: str(irs_uuid)})
        yield IRS(irs_uuid).associate_service_profile(sp_uuid)
        defer.returnValue(sp_uuid)

    @defer.inlineCallbacks
    def get_public_ids(self):
        pub_hash = yield self.get_columns_with_prefix_stripped(
                                                self.PUBLIC_ID_COLUMN_PREFIX)
        defer.returnValue(pub_hash.keys())

    @defer.inlineCallbacks
    def get_ifc(self):
        retval = yield self.get_column_value(self.IFC_COLUMN)
        defer.returnValue(retval)

    @defer.inlineCallbacks
    def get_irs(self):
        retval = yield self.get_column_value(self.IRS_COLUMN)
        defer.returnValue(retval)

    @defer.inlineCallbacks
    def associate_public_id(self, public_id):
        yield self.assert_row_exists()
        yield self.modify_columns({self.PUBLIC_ID_COLUMN_PREFIX + public_id:
                                                            NULL_COLUMN_VALUE})
        yield self.rebuild()

    @defer.inlineCallbacks
    def dissociate_public_id(self, public_id):
        yield self.delete_column(self.PUBLIC_ID_COLUMN_PREFIX + public_id)
        yield self.rebuild()

    @defer.inlineCallbacks
    def update_ifc(self, ifc):
        yield self.assert_row_exists()
        yield self.modify_columns({self.IFC_COLUMN: ifc})
        yield self.rebuild()

    @defer.inlineCallbacks
    def delete(self):
        _log.debug("Delete service profile %s" % self.row_key_str)
        public_ids = yield self.get_public_ids()
        for pub_id in public_ids:
            yield PublicID(pub_id).delete()

        irs_uuid = yield self.get_irs()
        IRS(irs_uuid).dissociate_service_profile(self.row_key)

        self.delete_row()

    @defer.inlineCallbacks
    def rebuild(self):
        _log.debug("Rebuild cache for parent of service profile %s" %
                   self.row_key_str)
        irs_uuid = yield self.get_irs()
        yield IRS(irs_uuid).rebuild()
