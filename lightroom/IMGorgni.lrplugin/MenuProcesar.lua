-- Biblioteca > Extras del plugin > "IMGorgni: procesar la cola ahora"
local LrDialogs = import "LrDialogs"
local LrTasks = import "LrTasks"

LrTasks.startAsyncTask(function()
	local n = require("Cola").procesar()
	if n == 0 then
		LrDialogs.message("IMGorgni", "No habia trabajos pendientes en la cola.", "info")
	else
		LrDialogs.message("IMGorgni", n .. " trabajo(s) procesados. El detalle queda en _lightroom\\plugin.log", "info")
	end
end)
