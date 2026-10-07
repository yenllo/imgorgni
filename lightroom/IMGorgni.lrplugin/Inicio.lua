--[[ Mientras Lightroom este abierto, cada minuto: si el script de la noche dejo la bandera
_lightroom\procesar_al_abrir, procesa la cola. Sin bandera no hace nada (abrir Lightroom de dia no
dispara ediciones). Vigila en vez de mirar una sola vez al abrir: el 7/10 la bandera estaba y la
cola no partio, y asi tambien sirve si Lightroom ya estaba abierto cuando aparece la bandera. ]]
local LrFileUtils = import "LrFileUtils"
local LrTasks = import "LrTasks"

local BANDERA = "C:\\Renzo\\Proyectos\\IMGorgni\\_lightroom\\procesar_al_abrir"
local LOG = "C:\\Renzo\\Proyectos\\IMGorgni\\_lightroom\\plugin.log"

local function log(msg)
	local f = io.open(LOG, "a")
	if f then f:write("inicio: ", msg, "\n"); f:close() end
end

log("plugin cargado")
LrTasks.startAsyncTask(function()
	LrTasks.sleep(30) -- que el catalogo termine de abrir
	while true do
		if LrFileUtils.exists(BANDERA) then
			LrFileUtils.delete(BANDERA)
			log("bandera encontrada, proceso la cola")
			local ok, err = LrTasks.pcall(function() require("Cola").procesar() end)
			if not ok then log("ERROR " .. tostring(err)) end
		end
		LrTasks.sleep(60)
	end
end)
