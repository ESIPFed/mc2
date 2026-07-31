We're essentially building something like MCP, to control a map, place assets on a map (polygons, geotiffs, points, paths, etc.)
Additionally we can contorl where we're zooming to, be it a point X zoomlevel, a constellation of geopsatial assets of interest (list of stuff), or just zooming a bit out. 
A user will be able to draw on this map as well.   

So, if I, lets say, plug this MCP into claude. For each session I have on claude it will create a sesion ID and a map url. I open up the map url, and the MCP will be able to contro lit. 

Or CLI based AI, or code based AI. 


What this is going look like, is a proxy, server thta servers a sessioned map. When a users asks to create such a map, they get a session ID, and a URL to a provisioned map. The map that's served has event processing/polling/etc code that checks the proxy server for map "events". Every event type has a custom handler for processing an event. For example if I poll and suddenly see zoom event, the map will know where to zoom to. Session events are saved. Same with a history of the files. 

The interface to the proxy server is going to have three manifestations best suited for AI. 
- a standard programming API. A python library. A python object per session maybe? 
- a CLI interface likely wrapping the python library. 
- an MCP likely wrapping the programming library.  

All of these will point to the proxy server that's running. 


Some functions:
    [Note that if you believe there is a better way, organize it, condense functions, invent new ones, you don't have to go with my taxonomy, it's more liek a guide. ]
    DEPENDING ON YOUR MAP PROVIDER, some of these may dissappear or may not be valid.  


    Zooms: 
        IT is important that zooms are an animation, not a sudden refresh of the page or jittery jump, make use of the libraries animated zoom/transition features. 
            Zoom to point: zooms to a GPS at an appropriate zoom level. 
            Zoom to polygons: zooms to a polygon appropriate zoom level to see polygon. 
            Zoom to assets: zooms to a list of geospatial assets making sure ALL of them are accomodated. 
            Zoom out: zooms out a zoom level. 

    Add Polygon: 
        Adding polygons to the map.
        One can place metadata to the asset. Title, Description, MD formatted content, iframe_info, misc (for when one clicks on a polygon)
            Add polygon str: adds a polygon to the map. The polygon is just a string of GEOJSON.
            Add polygon url: adds a polygon to the map. The polygon is a url to download GEOJSON from.
            Add polygon (animated): if you have animation features in your choice of provider allow the polygon to be animated (glowing (Fades in and then out periodically)) 
            
    Add Path:
        Add a path rather than a polygon. 
            Add path str(s): adds paths as a GEOJSON string. 
            Add path url(s): adds paths to the map. The path is a url to download GEOJSON from.
            Add path (animated): if you have animation features in your choice of provider allow the path to be animated. 
            
    Add ARC:
        Probably for advanced visualizers like DECKGL.
        ARC is composed of two points I would assume and some coloring. Whatever command you can think of here. Might just be two points, a color and thickness? 
        ARC animations
    
    
    Add Geotiff:
        Adding geotiffs to the map. 
        One can place metadata to the asset. Title, Description, MD formatted content, iframe_info, misc (for when one clicks on a polygon)
            Add geotiffs file: somehow send a file directly. The proxy server might download it and host it for the frontend if that's the case? 
            Add geotiffs url: somehow send a file via URL. Not sure about this what if the object manager in the proxy just kept the reference and the map interface knew how to react? 
            Add geotiff byecode_str: sends via bytecode. 

    Get Viewport:
        The AI might want to know what the user is looking at for their session. 
            Get Viewport: Returns BBOX bounds of the viewport of the map. Additionally a list of all Assets within that viewport. Maybe a few lists:
                -  References to fully contained items in that viewport
                - partial list: referencess for those that are clipped by the viewport. 
                - assets which are way bigger than the viewport. 
                - Maybe each of these should have flags about wether they're visible? (can toggle visibility through other tools)

    List assets: 
        List assets: Lists all assets. 
        Get user drawn assets: Important to the user will be that they will be able to draw their own polygons. 

    Delete Assets:
        Deletes assets by name. 

    Asset visibility:
        Toggle visibility. 

    Get Asset manager: 
        Returns a link to an asset manager/toggle pane. People would put this in an Iframe on their UI. 

    Can you think of any more that would be useful?  



SESSION MANAGEMENT: 
    Multiple users can edit the same map if they'd like, but they can't control each other's zoom. Perhaps there hsould be a map ID, as well as a user_session ID? 
    When a map is "RELOADED", it should NOT replay old animations and zooms. It SHOULD repopulate the map.  


CHOICE OF MAP: 
    Start with OpenLayers
    Then LEaflet
    Then Deckgl 

    Offer the ability to specify when the server is launched. 


About the overall UI:
    The map that's returned is just a map. No extra bells and whistles. Remember it will likely be embedded as an Iframe. We're going with pure map. 
    Any additional component (like the component to toggle visibility of assets) will be its own url or link to be embedded as an IFrame as seen fit, OR maybe a user would build their own? Not sure. 


