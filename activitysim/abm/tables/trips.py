# ActivitySim
# See full license in LICENSE.txt.

import logging

import pandas as pd

from activitysim.core import inject


logger = logging.getLogger(__name__)


@inject.table()
def trips_merged(trips, tours):
    return inject.merge_tables(trips.name, tables=[trips, tours])


inject.broadcast('tours', 'trips', cast_index=True, onto_on='tour_id')


@inject.table()
def bad_trips(trips):
    trips = trips.to_frame()
    if 'bad' in trips:
        return trips[trips.bad]
    else:
        return pd.DataFrame()
