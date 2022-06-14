import datetime
import json
import logging
import os
import random
import sys
import time
from datetime import timezone
from threading import Thread

import dash_leaflet as dl
import dash_leaflet.express as dlx
import requests
import schedule
from dash import Dash, dcc, html
from dash.dependencies import Input, Output
from dash_extensions.javascript import assign
from mtk_common import utils

LOGGER = logging.getLogger("ngsildmap")
LOGGER.setLevel(10)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(formatter)
sh.setLevel(10)
LOGGER.addHandler(sh)
LOGGER.info("NGSI-LD Map starting...")


def loadType2Attribs():
    splitted = defaultEntityTypeAttributeCombos.split(",")
    result = {}
    for entry in splitted:
        tmp = entry.split(";")
        if tmp[0] not in result:
            result[tmp[0]] = set()
        result[tmp[0]].add(tmp[1])
    return result


def getColorScales():
    tmp = os.getenv(
        "SCALES", "green;yellow;orange;red;red,blue;purple;brown,white;grey;black"
    ).split(",")
    result = []
    for entry in tmp:
        result.append(entry.split(";"))
    return result


defaultPollTime = os.getenv("POLL_TIME", 30)
defaultHost = os.getenv("DEFAULT_HOST", "http://cema.nlehd.de:2042")
defaultLimit = os.getenv("DEFAULT_LIMIT", 1000)
defaultAtContext = os.getenv(
    "DEFAULT_AT_CONTEXT",
    "https://raw.githubusercontent.com/smart-data-models/data-models/master/context.jsonld",
)
defaultEntityTypeAttributeCombos = os.getenv(
    "DEFAULT_TYPE_ATTRS_COMBOS", "EmissionObserved;co2"
)
defaultRange = int(os.getenv("DEFAULT_RANGE", 12 * 3600))
defaultMins = os.getenv("MINSCALES", "0,0").split(",")
defaultMaxs = os.getenv("MAXSCALES", "30,100").split(",")
defaultScaleUnits = os.getenv("SCALEUNITS", "g,m/s^2").split(",")
defaultPort = int(os.getenv("MAP_PORT", 8050))

colorScales = getColorScales()
initialBoundMinLat = 999999999999
initialBoundMaxLat = -999999999999
initialBoundMinLon = 999999999999
initialBoundMaxLon = -999999999999


chroma = "https://cdnjs.cloudflare.com/ajax/libs/chroma-js/2.1.0/chroma.min.js"  # js lib used for colors
type2Attribs = loadType2Attribs()


def getToolTip(entityId, properties):
    result = "<b>" + entityId + "</b><br><b>" + properties["type"] + "</b><br>"
    del properties["type"]
    del properties["location"]
    for key, value in properties.items():
        result = result + "<b>" + key + ":</b>"
        if value["type"] == "Relationship":
            result = result + value["object"]
        else:
            result = result + str(value["value"])
        result = result + "<br>"
    return result


def clearGeoJson(entities, attrib):
    if "features" not in entities:
        return None
    features = entities["features"]
    for feature in features:
        entityId = feature["id"]
        tooltip = getToolTip(entityId, feature["properties"])
        value = feature["properties"][attrib]["value"]
        feature["properties"] = {attrib: value, "tooltip": tooltip}
        if "geometry" in feature.keys():
            global initialBoundMinLat
            global initialBoundMaxLat
            global initialBoundMinLon
            global initialBoundMaxLon
            lon = feature["geometry"]["coordinates"][0]
            lat = feature["geometry"]["coordinates"][1]
            if lat > initialBoundMaxLat:
                initialBoundMaxLat = lat
            if lat < initialBoundMinLat:
                initialBoundMinLat = lat
            if lon > initialBoundMaxLon:
                initialBoundMaxLon = lon
            if lon < initialBoundMinLon:
                initialBoundMinLon = lon
    # print(json.dumps(entities, indent=4))
    return entities


def clearGeoJson_temporal(entities_temporal, attrib):
    features = {}
    # fix ngsi-ld bug
    for e in entities_temporal:
        for k, v in e.items():
            if type(v) == dict:
                e[k] = [v]
    results = []
    for e in entities_temporal:
        i = 0
        for v in e[attrib]:
            r = {}
            r["geometry"] = e["location"][i]["value"]
            r["id"] = e["id"]
            r["type"] = "Feature"
            r["properties"] = {attrib: v["value"], "tooltip": ""}
            results.append(r)
            i = i + 1
    features["features"] = results
    features["type"] = "FeatureCollection"
    return features


def leafCallback(n, entityTypeAttrib):
    LOGGER.debug(entityTypeAttrib)
    splitted = entityTypeAttrib.split(";")
    global defaultRange
    date = (
        datetime.datetime.now(timezone.utc) - datetime.timedelta(seconds=defaultRange)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    # print(defaultHost + '/ngsi-ld/v1/entities?type='+splitted[0]+'&limit=' + str(defaultLimit) + '&q=' + splitted[1] + '.observedAt>=' + date)
    entities = requests.get(
        defaultHost
        + "/ngsi-ld/v1/entities?type="
        + splitted[0]
        + "&limit="
        + str(defaultLimit)
        + "&q="
        + splitted[1]
        + ".observedAt>="
        + date,
        headers={
            "Link": "<"
            + defaultAtContext
            + '>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"',
            "Accept": "application/geo+json",
        },
    ).json()
    clearedGeoJson = clearGeoJson(entities, splitted[1])
    if clearedGeoJson == None:
        return None
    geobuf = dlx.geojson_to_geobuf(clearedGeoJson)
    return geobuf


def leafCallback_temporal(n, entityTypeAttrib):
    LOGGER.debug(entityTypeAttrib)
    splitted = entityTypeAttrib.split(";")
    global defaultRange
    # print(defaultHost + '/ngsi-ld/v1/entities?type='+splitted[0]+'&limit=' + str(defaultLimit) + '&q=' + splitted[1] + '.observedAt>=' + date)
    entities = utils.get_temporal_entities(
        entity_type=splitted[0], intervall=defaultRange, url=defaultHost, logger=LOGGER
    )
    clearedGeoJson = clearGeoJson_temporal(entities, splitted[1])
    if clearedGeoJson == None:
        return None
    LOGGER.debug("Received points %s", len(clearedGeoJson["features"]))
    geobuf = dlx.geojson_to_geobuf(clearedGeoJson)
    return geobuf


def getMinMax(clearedGeoJson, attrib):
    features = clearedGeoJson["features"]
    maxR = -999999999999999999999
    minR = 999999999999999999999
    for feature in features:
        value = feature["properties"][attrib]
        if value < minR:
            minR = value
        if value > maxR:
            maxR = value
    return [minR, maxR]


def initialMapSetup(app, entities, entityType):
    global type2Attribs
    global colorScales
    attribs = type2Attribs[entityType]
    count = 0
    mapLayers = []
    for attrib in attribs:
        clearedGeoJson = clearGeoJson(entities, attrib)
        if clearedGeoJson == None:
            geobuf = None
        else:
            geobuf = dlx.geojson_to_geobuf(clearedGeoJson)
        minMax = [int(defaultMins[count]), int(defaultMaxs[count])]
        colorscale = colorScales[count]
        colorbar = dl.Colorbar(
            colorscale=colorscale,
            width=20,
            height=150,
            min=minMax[0],
            max=minMax[1],
            unit=defaultScaleUnits[count],
        )
        count = count + 1
        # Geojson rendering logic, must be JavaScript as it is executed in clientside.
        point_to_layer = assign(
            """function(feature, latlng, context){
        const {min, max, colorscale, circleOptions, colorProp} = context.props.hideout;
        const csc = chroma.scale(colorscale).domain([min, max]).mode('lch');  // chroma lib to construct colorscale
        circleOptions.fillColor = csc(feature.properties[colorProp]);  // set color based on color prop.
        return L.circleMarker(latlng, circleOptions);  // sender a simple circle marker.
    }"""
        )
        # Create geojson.
        geojson = dl.GeoJSON(
            data=geobuf,
            id=entityType + ";" + attrib,
            format="geobuf",
            zoomToBounds=False,  # when true, zooms to bounds when data changes
            options=dict(pointToLayer=point_to_layer),  # how to draw points
            superClusterOptions=dict(radius=50),  # adjust cluster size
            hideout=dict(
                colorProp=attrib,
                circleOptions=dict(fillOpacity=1, stroke=False, radius=5),
                min=minMax[0],
                max=minMax[1],
                colorscale=colorscale,
            ),
        )
        mapLayers.append(geojson)
        mapLayers.append(colorbar)
    return [
        mapLayers,
        {
            "output": [entityType + ";" + attrib, "data"],
            "input": [
                ["interval-component", "n_intervals"],
                [entityType + ";" + attrib, "id"],
            ],
        },
    ]


def initialSetup(app):
    global type2Attribs
    global initialBoundMinLat
    global initialBoundMaxLat
    global initialBoundMinLon
    global initialBoundMaxLon
    mapLayers = []
    callbackTuples = []
    global defaultRange
    date = (
        datetime.datetime.now(timezone.utc) - datetime.timedelta(seconds=defaultRange)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    for entityType, attribs in type2Attribs.items():
        for attrib in attribs:
            mapSet = initialMapSetup(
                app,
                requests.get(
                    defaultHost
                    + "/ngsi-ld/v1/entities?type="
                    + entityType
                    + "&q="
                    + attrib
                    + ".observedAt>="
                    + date
                    + "&limit="
                    + str(defaultLimit),
                    headers={
                        "Link": "<"
                        + defaultAtContext
                        + '>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"',
                        "Accept": "application/geo+json",
                    },
                ).json(),
                entityType,
            )
            mapLayers = mapLayers + mapSet[0]
            callbackTuples.append(mapSet[1])
    mapLayers.append(dl.TileLayer())
    interval = dcc.Interval(
        id="interval-component",
        interval=defaultPollTime * 1000,  # in milliseconds
        n_intervals=0,
    )
    # print(str(initialBoundMinLat))
    # print(str(initialBoundMaxLat))
    # print(str(initialBoundMinLon))
    # print(str(initialBoundMaxLon))
    app.layout = html.Div(
        [
            dl.Map(
                mapLayers,
                bounds=[
                    [initialBoundMinLat, initialBoundMinLon],
                    [initialBoundMaxLat, initialBoundMaxLon],
                ],
                style={
                    "width": "100%",
                    "height": "80vh",
                    "margin": "auto",
                    "display": "block",
                    "position": "relative",
                },
                id="map",
            ),
            interval,
            html.Div(id="capital"),
        ]
    )
    for callbackTuple in callbackTuples:
        app.callback(
            Output(callbackTuple["output"][0], callbackTuple["output"][1]),
            [
                Input(callbackTuple["input"][0][0], callbackTuple["input"][0][1]),
                Input(callbackTuple["input"][1][0], callbackTuple["input"][1][1]),
            ],
        )(leafCallback)


# Create the app.
app = Dash(external_scripts=[chroma], prevent_initial_callbacks=True)
initialSetup(app)


if __name__ == "__main__":
    app.run_server(host="0.0.0.0", port=defaultPort)
