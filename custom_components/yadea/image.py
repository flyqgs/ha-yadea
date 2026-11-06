"""Image platform: official vehicle photo from the Yadea cloud snapshot.

The binding-bike snapshot carries `imgUrl` (Yadea CDN PNG per model).
Exposes it as a proper image entity so dashboards (picture cards) can
display each vehicle.
"""
from __future__ import annotations

import logging

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .coordinator import YadeaCoordinator

LOGGER = logging.getLogger(__package__)


class YadeaVehicleImage(ImageEntity):
    """Official vehicle picture."""

    _attr_translation_key = "vehicle_image"
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: YadeaCoordinator,
        vin: str,
        bike: dict,
    ) -> None:
        super().__init__(hass)
        self._coordinator = coordinator
        self.vin = vin
        self._url = bike.get("imgUrl") or ""
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{vin}_vehicle_image"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.vin)},
        )

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        """Fetch the picture once added.

        The Yadea CDN serves the PNG with Content-Type
        ``application/octet-stream``, so official URL loaders reject it.
        We download manually, sniff the magic bytes and store the result
        in BOTH attribute layouts: the legacy ``_image``/``_content_type``
        (HA <= 2025.x) and the ``_cached_image`` Image dataclass
        (HA 2026.x refactor) - whichever applies is picked up by core.
        """
        await super().async_added_to_hass()
        if not self._url:
            return
        try:
            session = async_get_clientsession(self.hass)
            resp = await session.get(self._url)
            data = await resp.read()
            is_png = data[:4] == b"\x89PNG"
            is_jpeg = data[:3] == b"\xff\xd8\xff"
            if resp.status == 200 and (is_png or is_jpeg):
                content_type = "image/png" if is_png else "image/jpeg"
                # New-style cache (components/image/entity.py refactor)
                try:
                    from homeassistant.components.image.entity import Image

                    self._cached_image = Image(
                        content_type=content_type, content=data
                    )
                except ImportError:
                    pass
                # Legacy attributes
                self._image = data
                self._content_type = content_type
                self._attr_content_type = content_type
                self._attr_image_last_updated = dt_util.utcnow()
                self.async_write_ha_state()
                LOGGER.info(
                    "车辆图片加载成功 (%s): %d bytes %s", self.vin, len(data), content_type
                )
            else:
                LOGGER.warning(
                    "车辆图片响应异常 (%s): http=%s bytes=%s", self.vin, resp.status, len(data)
                )
        except Exception as err:  # noqa: BLE001 - picture is cosmetic
            LOGGER.warning("加载车辆图片失败 (%s): %s", self.vin, err)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yadea vehicle images from a config entry."""
    coordinator: YadeaCoordinator = entry.runtime_data
    bikes = (coordinator.data or {}).get("bikes", [])

    entities: list[YadeaVehicleImage] = []
    for bike in bikes:
        vin = str(bike.get("vin", ""))
        if vin and bike.get("imgUrl"):
            entities.append(YadeaVehicleImage(hass, coordinator, vin, bike))
    async_add_entities(entities)
