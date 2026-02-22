/*
  Audit: Potential trailhead locations on cycle route relations
  
  Only looks at dedicated trail infrastructure (cycleway/path/footway),
  not on-road segments of cycle routes.
  
  Shows:
  1. Existing tagged trailheads (green circles)
  2. Parking near trails (red squares) - likely missing trailhead tags
  3. Bicycle parking near trails (orange circles)
  4. Picnic sites near trails (purple triangles)
  5. Info boards/maps near trails (blue diamonds)
  6. Off-road trail ways (gold lines)
  
  Area: Greater Philadelphia / Circuit Trails region

  Run this in https://overpass-turbo.eu/ to preview results.
*/

[out:json][timeout:120][bbox:39.85,-75.65,40.35,-74.85];

// Get all bicycle route relations
relation["type"="route"]["route"="bicycle"];
(._;)->.routes;

// Get only off-road ways (cycleway, path, footway) in those routes
.routes; way(r)["highway"~"cycleway|path|footway"];
(._;)->.trail_ways;

// Get nodes of only the off-road ways
.trail_ways; node(w);
(._;)->.trail_nodes;

// Existing trailheads
(
  node["highway"="trailhead"];
  node["tourism"="information"]["information"="trailhead"];
)->.existing_trailheads;

// Parking lots near trails
(
  node["amenity"="parking"](around.trail_nodes:50);
  way["amenity"="parking"](around.trail_nodes:50);
)->.nearby_parking;

// Bicycle parking near trails
node["amenity"="bicycle_parking"](around.trail_nodes:50)->.nearby_bike_parking;

// Picnic sites near trails
(
  node["tourism"="picnic_site"](around.trail_nodes:50);
  node["leisure"="picnic_table"](around.trail_nodes:50);
)->.nearby_picnic;

// Info boards/maps near trails
(
  node["tourism"="information"]["information"="board"](around.trail_nodes:50);
  node["tourism"="information"]["information"="map"](around.trail_nodes:50);
  node["tourism"="information"]["information"="guidepost"](around.trail_nodes:50);
)->.nearby_info;

// Output everything
(
  .existing_trailheads;
  .nearby_parking;
  .nearby_bike_parking;
  .nearby_picnic;
  .nearby_info;
  .trail_ways;
);
out body;
>;
out skel qt;

{{style:
  way { color: gold; width: 3; opacity: 0.6; }
  node[highway=trailhead],
  node[tourism=information][information=trailhead] { 
    color: green; fill-color: green; symbol-size: 12; symbol-shape: circle;
  }
  node[amenity=parking],
  way[amenity=parking] { 
    color: red; fill-color: red; symbol-size: 8; symbol-shape: square;
  }
  node[amenity=bicycle_parking] {
    color: orange; fill-color: orange; symbol-size: 8; symbol-shape: circle;
  }
  node[tourism=picnic_site],
  node[leisure=picnic_table] {
    color: purple; fill-color: purple; symbol-size: 8; symbol-shape: triangle;
  }
  node[tourism=information] {
    color: blue; fill-color: blue; symbol-size: 8; symbol-shape: diamond;
  }
}}
