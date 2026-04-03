import os
from dotenv import load_dotenv

load_dotenv()
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
LLAMA_BASE_URL = os.getenv("LLAMA_BASE_URL", "http://localhost:8080")

KNOWN_FOLDERS = {
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
    "appdata": "{3EB685DB-65F9-4CF6-A03A-E3EF65729F3D}",
    "localappdata": "{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "рабочий стол": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "документы": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "изображения": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "музыка": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "видео": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
    "данные приложений": "{3EB685DB-65F9-4CF6-A03A-E3EF65729F3D}",
    "локальные данные": "{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}",
    "загрузки": "{374DE290-123F-4565-9164-39C4925E467B}",
}

env_map = {
    "desktop": ("USERPROFILE", "Desktop"),
    "documents": ("USERPROFILE", "Documents"),
    "pictures": ("USERPROFILE", "Pictures"),
    "music": ("USERPROFILE", "Music"),
    "videos": ("USERPROFILE", "Videos"),
    "downloads": ("USERPROFILE", "Downloads"),
    "рабочий стол": ("USERPROFILE", "Desktop"),
    "документы": ("USERPROFILE", "Documents"),
    "изображения": ("USERPROFILE", "Pictures"),
    "музыка": ("USERPROFILE", "Music"),
    "видео": ("USERPROFILE", "Videos"),
    "загрузки": ("USERPROFILE", "Downloads"),
}
