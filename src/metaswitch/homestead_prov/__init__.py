# @file __init__.py
#
# Copyright (C) Metaswitch Networks 2016
# If license terms are provided to you in a COPYING file in the root directory
# of the source code repository by which you are accessing this code, then
# the license outlined in that COPYING file applies to your use.
# Otherwise no rights are granted except for those provided to you by
# Metaswitch Networks in a separate written agreement.

from .cache.cache import Cache
from .provisioning.handlers.private import PrivateHandler, PrivateAllIrsHandler, PrivateOneIrsHandler, PrivateAllPublicIdsHandler
from .provisioning.handlers.irs import AllIRSHandler, IRSHandler, IRSAllPublicIDsHandler, IRSAllPrivateIDsHandler, IRSPrivateIDHandler
from .provisioning.handlers.service_profile import AllServiceProfilesHandler, ServiceProfileHandler, SPAllPublicIDsHandler, SPPublicIDHandler, SPFilterCriteriaHandler
from .provisioning.handlers.public import PublicIDServiceProfileHandler, PublicIDIRSHandler, PublicIDPrivateIDHandler, AllPublicIDsHandler

from .cache.db import CacheModel
from .provisioning.models import PrivateID, IRS, ServiceProfile, PublicID, ProvisioningModel
from metaswitch.crest.api.ping import PingHandler

# Regex that matches any path element (covers anything that isn't a slash).
ANY = '([^/]+)'

AUTHTYPE = "(aka|digest)"

# Regex that matches a uuid.
HEX = '[a-fA-F0-9]'
UUID = '(%s{8}-%s{4}-%s{4}-%s{4}-%s{12})' % (HEX, HEX, HEX, HEX, HEX)

# Routes for application. Each route consists of:
# - The actual route regex, with capture groups for parameters that will be
# passed to the the Handler.
# - The Handler to process the request.
ROUTES = [
    #
    # Private ID provisioning.
    #

    # Get, create or destory a specific private ID.
    (r'/private/'+ANY+r'/?', PrivateHandler),

    # List all the implicit registration sets a private ID can authenticate.
    (r'/private/'+ANY+r'/associated_implicit_registration_sets/?', PrivateAllIrsHandler),

    # Associate / dissociate a private ID with an IRS.
    (r'/private/'+ANY+r'/associated_implicit_registration_sets/'+UUID+r'/?', PrivateOneIrsHandler),

    # List all the public IDs associated with a private ID (i.e. those that are
    # members of the private ID's implicit registration sets.
    (r'/private/'+ANY+r'/associated_public_ids/?', PrivateAllPublicIdsHandler),

    #
    # Implicit Registration Set provisioning.
    #

    # Create a new implicit registration set.
    (r'/irs/?', AllIRSHandler),

    # Delete a specific IRS.
    (r'/irs/'+UUID+r'/?', IRSHandler),

    # List all public IDs that are members of a specific IRS.
    (r'/irs/'+UUID+r'/public_ids/?', IRSAllPublicIDsHandler),

    # List all private IDs that can authenticate public IDs in this IRS.
    (r'/irs/'+UUID+r'/private_ids/?', IRSAllPrivateIDsHandler),

    # Associate a private ID with an IRS.
    (r'/irs/'+UUID+r'/private_ids/'+ANY+r'/?', IRSPrivateIDHandler),

    #
    # Service profile provisionng.
    #
    # In the class naming scheme, "all" refers to all objects in the parent
    # container (all service profiles in an IRS, or all public IDs in a
    # profile).
    #

    # Create a new service profile in the IRS.
    (r'/irs/'+UUID+r'/service_profiles/?', AllServiceProfilesHandler),

    # Delete a specific service profile from the IRS.
    (r'/irs/'+UUID+r'/service_profiles/'+UUID+'/?', ServiceProfileHandler),

    # List all the public IDs in a specific service profile.
    (r'/irs/'+UUID+r'/service_profiles/'+UUID+'/public_ids/?', SPAllPublicIDsHandler),

    # Create or delete a public ID (within a specific service profile).
    (r'/irs/'+UUID+r'/service_profiles/'+UUID+'/public_ids/'+ANY+r'/?', SPPublicIDHandler),

    # Set/get the initial filter criteria for a specific service profile.
    (r'/irs/'+UUID+r'/service_profiles/'+UUID+'/filter_criteria/?', SPFilterCriteriaHandler),

    #
    # Read-only public ID interface.
    #

    # Redirect to the public ID's service profile.
    (r'/public/'+ANY+r'/service_profile/?', PublicIDServiceProfileHandler),

    # Redirect to the public ID's IRS.
    (r'/public/'+ANY+r'/irs/?', PublicIDIRSHandler),

    # List all private IDs that can authenticate this public ID.
    (r'/public/'+ANY+r'/associated_private_ids/?', PublicIDPrivateIDHandler),

    # List all public IDs
    (r'/public/?', AllPublicIDsHandler),
]

# List of all the tables used by homestead.
TABLES = [PrivateID, IRS, ServiceProfile, PublicID]

def initialize(application):
    """Module initialization"""

    # Create a cache and register it with the provisioning models (so they keep
    # the denormalized tables in sync with the normalized ones).
    application.cache = Cache()
    ProvisioningModel.register_cache(application.cache)

    # Connect to the cache and provisioning databases. Register the cassandra
    # factories with the PingHandler so that connectivity to cassandra is
    # checked when crest is pinged.
    ProvisioningModel.start_connection()
    PingHandler.register_cass_factory(ProvisioningModel.get_cass_factory())

    CacheModel.start_connection()
    PingHandler.register_cass_factory(CacheModel.get_cass_factory())
