/* CRM 前端脚本 —— 保持极简，页面交互以服务端渲染为主 */

/**
 * 用浏览器定位填充经纬度输入框。
 * 定位失败时保留原值（通常预填了客户登记坐标），不阻断提交。
 */
function fillGeolocation(lngId, latId, statusId) {
  const status = statusId ? document.getElementById(statusId) : null;
  if (!navigator.geolocation) {
    if (status) status.textContent = "当前环境不支持定位，将使用客户登记坐标";
    return;
  }
  if (status) status.textContent = "正在获取定位…";
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      document.getElementById(lngId).value = pos.coords.longitude.toFixed(7);
      document.getElementById(latId).value = pos.coords.latitude.toFixed(7);
      if (status) status.textContent = "定位成功，精度约 " + Math.round(pos.coords.accuracy) + " 米";
    },
    (err) => {
      if (status) status.textContent = "定位失败（" + err.message + "），将使用客户登记坐标";
    },
    { enableHighAccuracy: true, timeout: 8000 }
  );
}

/** 选择客户后，把该客户的登记坐标预填到表单，便于无定位环境下走通流程 */
function prefillCustomerCoords(selectId, lngId, latId) {
  const select = document.getElementById(selectId);
  if (!select) return;
  const apply = () => {
    const opt = select.options[select.selectedIndex];
    if (!opt) return;
    const lng = opt.getAttribute("data-lng");
    const lat = opt.getAttribute("data-lat");
    if (lng && !document.getElementById(lngId).value) {
      document.getElementById(lngId).value = lng;
    }
    if (lat && !document.getElementById(latId).value) {
      document.getElementById(latId).value = lat;
    }
  };
  select.addEventListener("change", apply);
  apply();
}

/** 二次确认，用于回退/关闭等不可逆操作 */
function confirmSubmit(form, message) {
  return window.confirm(message);
}
