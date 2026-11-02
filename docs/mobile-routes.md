# Mobile API route inventory

This is a static inventory of API path literals recovered from Android CellGate 4.0.61. It is not proof that every route is enabled for an end-user account. Four original request contracts come from the public reference integration at commit `2f72a80e84a350bd9be102719190b84c5e3e3083`; endpoint literals also occur in the downloaded app. Mobile login, location/device discovery, and SIP provisioning have subsequently succeeded against the owner's account. Gate commands remain untested. See [authenticated results](sip-provisioning.md).

Origin: `https://cellgate.zapopen.com`.

`headers` means POST with request fields carried in headers. `form` means POST with Bearer authorization and an `application/x-www-form-urlencoded` body. A dash means the request contract remains untraced; no method is inferred from the endpoint name. Classification outside the five implemented routes is by endpoint name only.

| Path literal | Category | Method / format | Integration |
| --- | --- | --- | --- |
| `/api/cellcam/checkMandatoryUpdate` | other-app-operation | — | Research only |
| `api/CellCam/BLEKeyRegisteredOnTheDevice` | credentials-visitors-access | — | Research only |
| `api/CellCam/CameraImage` | camera-snapshot-or-credit | — | Research only |
| `api/CellCam/CellCamLogout/` | authentication-session-call | — | Research only |
| `api/CellCam/CheckCode/` | credentials-visitors-access | — | Research only |
| `api/CellCam/CheckDeviceAcknowledgement/?moduleID=` | gate-control-state | — | Research only |
| `api/CellCam/CheckUserAlreadyLogin` | authentication-session-call | — | Research only |
| `api/CellCam/Code/` | credentials-visitors-access | — | Research only |
| `api/CellCam/DealerLogin` | dealer-provisioning | — | Research only |
| `api/CellCam/DealerLoginByCode` | dealer-provisioning | — | Research only |
| `api/CellCam/DealerLogout/` | dealer-provisioning | — | Research only |
| `api/CellCam/deviceInfo` | discovery-configuration | — | Research only |
| `api/CellCam/DevicesList` | discovery-configuration | POST / form | Implemented; live-validated |
| `api/CellCam/DeviceTokenUpdate` | authentication-session-call | — | Research only |
| `api/CellCam/EnableDisableMotionSensor` | credentials-visitors-access | — | Research only |
| `api/CellCam/EnableElevatorAccess` | credentials-visitors-access | — | Research only |
| `api/CellCam/externalPortCharges` | billing | — | Research only |
| `api/CellCam/ForgetPassword` | other-app-operation | — | Research only |
| `api/CellCam/GateCommand` | gate-control-state | POST / form | Implemented; synthetic tests |
| `api/CellCam/GetAllAccessCode` | credentials-visitors-access | — | Research only |
| `api/CellCam/GetAppUpdatePriority` | other-app-operation | — | Research only |
| `api/CellCam/GetBLERegistrationStatus` | credentials-visitors-access | — | Research only |
| `api/CellCam/GetBLEVirtualAccessKey` | credentials-visitors-access | — | Research only |
| `api/CellCam/GetCountryAndUSState/` | other-app-operation | — | Research only |
| `api/CellCam/GetDealerAccountLocations?accountId=` | dealer-provisioning | — | Research only |
| `api/CellCam/GetDealerAccountsList` | dealer-provisioning | — | Research only |
| `api/CellCam/GetDealerCompanyAccountsList` | dealer-provisioning | — | Research only |
| `api/CellCam/GetDealerInfoSuggestions?phoneNumber=` | dealer-provisioning | — | Research only |
| `api/CellCam/GetDealerPropertyLocationsList?accountID=` | dealer-provisioning | — | Research only |
| `api/CellCam/GetDeviceNameSuggestions/` | other-app-operation | — | Research only |
| `api/CellCam/GetECDHKey` | other-app-operation | — | Research only |
| `api/CellCam/GetEnabledServiceInfo` | credentials-visitors-access | — | Research only |
| `api/CellCam/GetGatewayOnlineStatus?gatewayEUI=` | other-app-operation | — | Research only |
| `api/CellCam/getLatestDFU` | other-app-operation | — | Research only |
| `api/CellCam/GetLiveCredits/` | camera-snapshot-or-credit | — | Research only |
| `api/CellCam/GetMapLocationList` | discovery-configuration | POST / headers | Implemented; live-validated |
| `api/CellCam/GetModuleIDForInstallLocation?propertyLocationID=` | discovery-configuration | — | Research only |
| `api/CellCam/GetModuleportConfigurations?ModuleID=` | discovery-configuration | — | Research only |
| `api/CellCam/GetMotionSensorSettings` | other-app-operation | — | Research only |
| `api/CellCam/GetNotificationSettings/` | other-app-operation | — | Research only |
| `api/CellCam/GetOmniPointGateCommandStatus` | gate-control-state | — | Research only |
| `api/CellCam/GetOmnipointGateStatus` | gate-control-state | — | Research only |
| `api/CellCam/GetPortAccessInfo` | discovery-configuration | — | Research only |
| `api/CellCam/getPresetRestrictionTemplate` | other-app-operation | — | Research only |
| `api/CellCam/getPresetTemplateDetails/?Id=` | other-app-operation | — | Research only |
| `api/CellCam/GetPropertyLocationDevice` | discovery-configuration | — | Research only |
| `api/CellCam/GetSensorStatus?ModuleID=` | gate-control-state | — | Research only |
| `api/CellCam/GetShareMessage` | other-app-operation | — | Research only |
| `api/CellCam/getSingleFamilyPendingPaymentInfo` | billing | — | Research only |
| `api/CellCam/GetSipInformationForUser` | authentication-session-call | POST / headers | Implemented; live-validated |
| `api/CellCam/GetU3GateStatus` | gate-control-state | — | Research only |
| `api/cellcam/getUserIntent` | other-app-operation | — | Research only |
| `api/CellCam/GetWiegandCode` | credentials-visitors-access | — | Research only |
| `api/CellCam/KitRegistration` | dealer-provisioning | — | Research only |
| `api/CellCam/LastSeenCameraImage` | camera-snapshot-or-credit | — | Research only |
| `api/CellCam/LocationsForUser` | discovery-configuration | — | Research only |
| `api/CellCam/Login` | authentication-session-call | POST / headers | Implemented; live-validated |
| `api/CellCam/ManualScheduleAuthorization` | other-app-operation | — | Research only |
| `api/CellCam/MoveDeviceByQRScan/` | other-app-operation | — | Research only |
| `api/CellCam/OmnipointTriggerGate` | gate-control-state | — | Research only |
| `api/CellCam/PaymentPlan` | billing | — | Research only |
| `api/CellCam/paysecure` | billing | — | Research only |
| `api/CellCam/refresh` | authentication-session-call | — | Research only |
| `api/CellCam/RefreshSIPToken/` | authentication-session-call | — | Research only |
| `api/CellCam/RegCard/` | credentials-visitors-access | — | Research only |
| `api/CellCam/ResetPassword` | other-app-operation | — | Research only |
| `api/CellCam/rootedDevicesAndOSTracking` | other-app-operation | — | Research only |
| `api/CellCam/SaveAccount/` | other-app-operation | — | Research only |
| `api/CellCam/SaveKitRegDealerInfo/` | dealer-provisioning | — | Research only |
| `api/CellCam/SavePSTNNumbers` | other-app-operation | — | Research only |
| `api/CellCam/SaveUserAccessCode` | credentials-visitors-access | — | Research only |
| `api/CellCam/SendOTP/` | credentials-visitors-access | — | Research only |
| `api/CellCam/U3TriggerGate` | gate-control-state | — | Research only |
| `api/CellCam/UpdateInputActiveState` | other-app-operation | — | Research only |
| `api/CellCam/UpdateKitInstallationWizardLogs` | dealer-provisioning | — | Research only |
| `api/CellCam/UpdateModuleportConfigurations` | discovery-configuration | — | Research only |
| `api/CellCam/UpdateModuleportConfigurationsEPMV2` | discovery-configuration | — | Research only |
| `api/CellCam/UpdateMotionSensor` | other-app-operation | — | Research only |
| `api/CellCam/UpdateMotionSensorSettings` | other-app-operation | — | Research only |
| `api/CellCam/UpdateNotificationSettings` | other-app-operation | — | Research only |
| `api/CellCam/UpdatePayment/` | billing | — | Research only |
| `api/CellCam/UpdateUserTimeFrame` | other-app-operation | — | Research only |
| `api/CellCam/ValidateOTP/` | credentials-visitors-access | — | Research only |
| `api/CellCam/VerifyDevice/` | other-app-operation | — | Research only |
| `api/Dealer/ForgotPassword` | dealer-provisioning | — | Research only |
| `api/DownloadAndShare/?filename=` | other-app-operation | — | Research only |
| `api/GetPaymentGatewayType` | billing | — | Research only |
| `api/ImageUpload/?filename=` | other-app-operation | — | Research only |
| `api/QRCode/CreateUpdateInvitation` | credentials-visitors-access | — | Research only |
| `api/QRCode/CreateUpdatePinCodeInvite` | credentials-visitors-access | — | Research only |
| `api/QRCode/CreateUpdateRecurringInvite` | credentials-visitors-access | — | Research only |
| `api/QRCode/CreateUpdateSingleUseQRCode` | credentials-visitors-access | — | Research only |
| `api/QRCode/DeactivateInvite` | credentials-visitors-access | — | Research only |
| `api/QRCode/DeactivatePINCodeInvite` | credentials-visitors-access | — | Research only |
| `api/QRCode/DeactivateSingleUseInvite` | credentials-visitors-access | — | Research only |
| `api/QRCode/GetAccountDevicesForQrCode?InviteTypeID=` | credentials-visitors-access | — | Research only |
| `api/QRcode/GetCopyShareInfo?inviteID=` | credentials-visitors-access | — | Research only |
| `api/QRCode/GetPinCodeShareLink` | credentials-visitors-access | — | Research only |
| `api/QRCode/GetRecurringShareLink` | credentials-visitors-access | — | Research only |
| `api/QRCode/GetShareInfoForInvite` | credentials-visitors-access | — | Research only |
| `api/QRCode/GetShareInfoForSingleUsePINInvite` | credentials-visitors-access | — | Research only |
| `api/QRCode/GetShareInfoForSingleUseQRInvite` | credentials-visitors-access | — | Research only |
| `api/QRCode/GetSingleUseQRcodeShareLink` | credentials-visitors-access | — | Research only |
| `api/QRCode/ReactivatePinCode` | credentials-visitors-access | — | Research only |
| `api/QRCode/ReactivateQRCode` | credentials-visitors-access | — | Research only |
| `api/QRCode/ReactivateRecurringInvite` | credentials-visitors-access | — | Research only |
| `api/QRCode/SearchVirtualKeys?SearchString=` | credentials-visitors-access | — | Research only |
| `api/QRCode/UpdatePinCodeShareStatus` | credentials-visitors-access | — | Research only |
| `api/QRCode/UpdateRecurringShareStatus` | credentials-visitors-access | — | Research only |
| `api/QRCode/UpdateSingleUseQRCodeShareStatus` | credentials-visitors-access | — | Research only |
| `api/reviewTracking/statusUpdate` | other-app-operation | — | Research only |
| `api/user/GetUserAccountDeleteInfo` | other-app-operation | — | Research only |
| `api/user/UserAccountDelete` | other-app-operation | — | Research only |
| `api/VirtualKey/GetPinKeyInvites` | credentials-visitors-access | — | Research only |
| `api/VirtualKey/GetQRKeyInvites` | credentials-visitors-access | — | Research only |

Exact source offsets, fields, and response paths are in [mobile-routes.json](mobile-routes.json). The native Java client also declares `POST api/validate-integrity`; it is an app-integrity operation, not an implemented Home Assistant API route.

No snapshot, billing, account deletion, provisioning, or credential-management operation is exposed by the integration. Call handling uses Linphone SIP separately from these HTTP requests. `GetSipInformationForUser` is now implemented and live-validated. The separate `RefreshSIPToken/` route remains untraced and is not called. SIP registration is not implemented.

## SIP method observation

On November 2, 2026, an unauthenticated `OPTIONS /api/CellCam/GetSipInformationForUser` received HTTP 405 with `Allow: POST`. A POST without credentials, identifiers, or a body received HTTP 401 and an authorization-denied message. This confirms the live route/method and authentication requirement; it does not establish request fields, session concurrency, returned SIP credentials, or successful call handling. No authenticated mobile request or SIP registration was performed.

The owner has selected mobile-only work for now. The portal `/Play` investigation is deferred. The active integration continues to use only `cellgate.zapopen.com/api/CellCam`.

The later authenticated POST to `GetSipInformationForUser` succeeded with the mobile Bearer token and AppVersion `17`, without a body or an active call. Its response includes `data.sipInfo.account`. The same account data is also present in the successful mobile login response.
