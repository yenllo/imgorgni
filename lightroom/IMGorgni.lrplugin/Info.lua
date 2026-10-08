--[[ IMGorgni: edicion automatica en Lightroom Classic (1/10/2026).
Instalar una vez: Archivo > Administrador de plugins > Anadir > esta carpeta (IMGorgni.lrplugin).
Que hace: ver Cola.lua. ]]
return {
	LrSdkVersion = 13.0,
	LrSdkMinimumVersion = 10.0,
	LrToolkitIdentifier = "cl.rbfotos.imgorgni",
	LrPluginName = "IMGorgni - edicion automatica",
	LrInitPlugin = "Inicio.lua",
	-- Sin esto Lightroom carga el plugin recien al usar su menu, y Inicio.lua no corre al abrir:
	-- por eso la cola no partio sola el 7/10 ni el 8/10 (plugin.log sin "inicio: plugin cargado").
	LrForceInitPlugin = true,
	LrLibraryMenuItems = {
		{ title = "IMGorgni: procesar la cola ahora", file = "MenuProcesar.lua" },
	},
	VERSION = { major = 1, minor = 0, revision = 0 },
}
