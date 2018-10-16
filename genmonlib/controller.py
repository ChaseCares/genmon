#!/usr/bin/env python
#-------------------------------------------------------------------------------
#    FILE: controller.py
# PURPOSE: Controller Specific Detils for Base Class
#
#  AUTHOR: Jason G Yates
#    DATE: 24-Apr-2018
#
# MODIFICATIONS:
#
# USAGE: This is the base class of generator controllers. LogError or FatalError
#   should be used to log errors or fatal errors.
#
#-------------------------------------------------------------------------------

import threading, datetime, collections, os, time
# NOTE: collections OrderedDict is used for dicts that are displayed to the UI


import mysupport, mypipe, mythread

class GeneratorController(mysupport.MySupport):
    #---------------------GeneratorController::__init__-------------------------
    def __init__(self,
        log,
        newinstall = False,
        simulation = False,
        simulationfile = None,
        message = None,
        feedback = None,
        ConfigFilePath = None,
        config = None):

        super(GeneratorController, self).__init__(simulation = simulation)
        self.log = log
        self.NewInstall = newinstall
        self.Simulation = simulation
        self.SimulationFile = simulationfile
        self.FeedbackPipe = feedback
        self.MessagePipe = message
        self.config = config
        if ConfigFilePath == None:
            self.ConfigFilePath = "/etc/"
        else:
            self.ConfigFilePath = ConfigFilePath


        self.Address = None
        self.SerialPort = "/dev/serial0"
        self.BaudRate = 9600
        self.ModBus = None
        self.InitComplete = False
        self.IsStopping = False
        self.InitCompleteEvent = threading.Event() # Event to signal init complete
        self.CheckForAlarmEvent = threading.Event() # Event to signal checking for alarm
        self.Registers = {}         # dict for registers and values
        self.NotChanged = 0         # stats for registers
        self.Changed = 0            # stats for registers
        self.TotalChanged = 0.0     # ratio of changed ragisters
        self.EnableDebug = False    # Used for enabeling debugging
        self.UseMetric = False
        self.OutageLog = os.path.dirname(os.path.dirname(os.path.realpath(__file__))) + "/outage.txt"
        self.PowerLogMaxSize = 15       # 15 MB max size
        self.PowerLog =  os.path.dirname(os.path.dirname(os.path.realpath(__file__))) + "/kwlog.txt"
        self.TileList = []        # Tile list for GUI

        if self.Simulation:
            self.LogLocation = "./"
        else:
            self.LogLocation = "/var/log/"
        self.bDisplayUnknownSensors = False
        self.SlowCPUOptimization = False
        self.UtilityVoltsMin = 0    # Minimum reported utility voltage above threshold
        self.UtilityVoltsMax = 0    # Maximum reported utility voltage above pickup
        self.SystemInOutage = False         # Flag to signal utility power is out
        self.TransferActive = False         # Flag to signal transfer switch is allowing gen supply power
        self.ControllerSelected = None
        self.SiteName = "Home"
        # The values "Unknown" are checked to validate conf file items are found
        self.FuelType = "Unknown"
        self.NominalFreq = "Unknown"
        self.NominalRPM = "Unknown"
        self.NominalKW = "Unknown"
        self.Model = "Unknown"
        self.EngineDisplacement = "Unknown"
        self.TankSize = None

        self.ProgramStartTime = datetime.datetime.now()     # used for com metrics
        self.OutageStartTime = self.ProgramStartTime        # if these two are the same, no outage has occured
        self.LastOutageDuration = self.OutageStartTime - self.OutageStartTime

        try:
            if self.config != None:
                if self.config.HasOption('sitename'):
                    self.SiteName = self.config.ReadValue('sitename')

                if self.config.HasOption('port'):
                    self.SerialPort = self.config.ReadValue('port')

                if self.config.HasOption('loglocation'):
                    self.LogLocation = self.config.ReadValue('loglocation')

                if self.config.HasOption('optimizeforslowercpu'):
                    self.SlowCPUOptimization = self.config.ReadValue('optimizeforslowercpu', return_type = bool)
                # optional config parameters, by default the software will attempt to auto-detect the controller
                # this setting will override the auto detect

                if self.config.HasOption('metricweather'):
                    self.UseMetric = self.config.ReadValue('metricweather', return_type = bool)

                if self.config.HasOption('enabledebug'):
                    self.EnableDebug = self.config.ReadValue('enabledebug', return_type = bool)

                if self.config.HasOption('displayunknown'):
                    self.bDisplayUnknownSensors = self.config.ReadValue('displayunknown', return_type = bool)
                if self.config.HasOption('outagelog'):
                    self.OutageLog = self.config.ReadValue('outagelog')

                if self.config.HasOption('kwlog'):
                    self.PowerLog = self.config.ReadValue('kwlog')
                if self.config.HasOption('kwlogmax'):
                    self.PowerLogMaxSize = self.config.ReadValue('kwlogmax', return_type = int)

                if self.config.HasOption('nominalfrequency'):
                    self.NominalFreq = self.config.ReadValue('nominalfrequency')
                if self.config.HasOption('nominalRPM'):
                    self.NominalRPM = self.config.ReadValue('nominalRPM')
                if self.config.HasOption('nominalKW'):
                    self.NominalKW = self.config.ReadValue('nominalKW')
                if self.config.HasOption('model'):
                    self.Model = self.config.ReadValue('model')

                if self.config.HasOption('controllertype'):
                    self.ControllerSelected = self.config.ReadValue('controllertype')

                if self.config.HasOption('fueltype'):
                    self.FuelType = self.config.ReadValue('fueltype')

                if self.config.HasOption('tanksize'):
                    self.TankSize = self.config.ReadValue('tanksize')

                self.SmartSwitch = self.config.ReadValue('smart_transfer_switch', return_type = bool, default = False)

                self.UseSerialTCP = self.config.ReadValue('use_serial_tcp', return_type = bool, default = False)
                self.SerialTCPAddress = self.config.ReadValue('serial_tcp_address', return_type = str, default = None)
                self.SerialTCPPort = self.config.ReadValue('serial_tcp_port', return_type = int, default = None, NoLog = True)

        except Exception as e1:
            if not reload:
                self.FatalError("Missing config file or config file entries: " + str(e1))
            else:
                self.LogErrorLine("Error reloading config file" + str(e1))


    #----------  GeneratorController:StartCommonThreads-------------------------
    # called after get config file, starts threads common to all controllers
    def StartCommonThreads(self):

        self.Threads["CheckAlarmThread"] = mythread.MyThread(self.CheckAlarmThread, Name = "CheckAlarmThread")
        # start read thread to process incoming data commands
        self.Threads["ProcessThread"] = mythread.MyThread(self.ProcessThread, Name = "ProcessThread")

        if self.EnableDebug:        # for debugging registers
            self.Threads["DebugThread"] = mythread.MyThread(self.DebugThread, Name = "DebugThread")

        # start thread for kw log
        self.Threads["PowerMeter"] = mythread.MyThread(self.PowerMeter, Name = "PowerMeter")

    # ---------- GeneratorController:ProcessThread------------------------------
    #  read registers, remove items from Buffer, form packets, store register data
    def ProcessThread(self):

        try:
            self.ModBus.Flush()
            self.InitDevice()
            if self.IsStopping:
                return
            while True:
                try:
                    self.MasterEmulation()
                    if self.IsStopSignaled("ProcessThread"):
                        break
                    if self.IsStopping:
                        break
                except Exception as e1:
                    self.LogErrorLine("Error in Controller ProcessThread (1), continue: " + str(e1))
        except Exception as e1:
            self.LogErrorLine("Exiting Controller ProcessThread (2)" + str(e1))

    # ---------- GeneratorController:CheckAlarmThread---------------------------
    #  When signaled, this thread will check for alarms
    def CheckAlarmThread(self):

        time.sleep(.25)
        while True:
            try:
                if self.WaitForExit("CheckAlarmThread", 0.25):  #
                    return

                if self.CheckForAlarmEvent.is_set():
                    self.CheckForAlarmEvent.clear()
                    self.CheckForAlarms()

            except Exception as e1:
                self.LogErrorLine("Error in  CheckAlarmThread" + str(e1))

    #----------  GeneratorController:DebugThread--------------------------------
    def DebugThread(self):

        if not self.EnableDebug:
            return
        time.sleep(.25)

        if not self.ControllerSelected == None or not len(self.ControllerSelected) or self.ControllerSelected == "generac_evo_nexus":
            MaxReg = 0x400
        else:
            MaxReg == 0x400
        self.InitCompleteEvent.wait()

        if self.IsStopping:
            return
        self.LogError("Debug Enabled")
        self.FeedbackPipe.SendFeedback("Debug Thread Starting", FullLogs = True, Always = True, Message="Starting Debug Thread")
        TotalSent = 0

        RegistersUnderTest = collections.OrderedDict()
        RegistersUnderTestData = ""

        while True:

            if self.IsStopSignaled("DebugThread"):
                return
            if TotalSent >= 5:
                self.FeedbackPipe.SendFeedback("Debug Thread Finished", Always = True, FullLogs = True, Message="Finished Debug Thread")
                if self.WaitForExit("DebugThread", 1):  #
                    return
                continue
            try:
                for Reg in range(0x0 , MaxReg):
                    if self.WaitForExit("DebugThread", 0.25):  #
                        return
                    Register = "%04x" % Reg
                    NewValue = self.ModBus.ProcessMasterSlaveTransaction(Register, 1, ReturnValue = True)
                    if not len(NewValue):
                        continue
                    OldValue = RegistersUnderTest.get(Register, "")
                    if OldValue == "":
                        RegistersUnderTest[Register] = NewValue        # first time seeing this register so add it to the list
                    elif NewValue != OldValue:
                        BitsChanged, Mask = self.GetNumBitsChanged(OldValue, NewValue)
                        RegistersUnderTestData += "Reg %s changed from %s to %s, Bits Changed: %d, Mask: %x, Engine State: %s\n" % \
                                (Register, OldValue, NewValue, BitsChanged, Mask, self.GetEngineState())
                        RegistersUnderTest[Register] = Value        # update the value

                msgbody = "\n"
                for Register, Value in RegistersUnderTest.items():
                    msgbody += self.printToString("%s:%s" % (Register, Value))

                self.FeedbackPipe.SendFeedback("Debug Thread (Registers)", FullLogs = True, Always = True, Message=msgbody, NoCheck = True)
                if len(RegistersUnderTestData):
                    self.FeedbackPipe.SendFeedback("Debug Thread (Changes)", FullLogs = True, Always = True, Message=RegistersUnderTestData, NoCheck = True)
                RegistersUnderTestData = "\n"
                TotalSent += 1

            except Exception as e1:
                self.LogErrorLine("Error in DebugThread: " + str(e1))

    #------------ GeneratorController:GetRegisterValueFromList -----------------
    def GetRegisterValueFromList(self,Register):

        return self.Registers.get(Register, "")

    #-------------GeneratorController:GetParameterBit---------------------------
    def GetParameterBit(self, Register, Mask, OnLabel = None, OffLabel = None):

        try:
            Value =  self.GetRegisterValueFromList(Register)
            if not len(Value):
                return ""

            IntValue = int(Value, 16)

            if OnLabel == None or OffLabel == None:
                return self.BitIsEqual(IntValue, Mask, Mask)
            elif self.BitIsEqual(IntValue, Mask, Mask):
                return OnLabel
            else:
                return OffLabel
        except Exception as e1:
            self.LogErrorLine("Error in GetParameterBit: " + str(e1))
            return ""

    #-------------GeneratorController:GetParameterLong--------------------------
    def GetParameterLong(self, RegisterLo, RegisterHi, Label = None, Divider = None, ReturnInt = False):

        try:
            if not Label == None:
                LabelStr = Label
            else:
                LabelStr = ""

            ValueLo = self.GetParameter(RegisterLo)
            ValueHi = self.GetParameter(RegisterHi)

            if not len(ValueLo) or not len(ValueHi):
                if ReturnInt:
                    return 0
                else:
                    return ""

            IntValueLo = int(ValueLo)
            IntValueHi = int(ValueHi)

            IntValue = IntValueHi << 16 | IntValueLo

            if ReturnInt:
                return IntValue

            if not Divider == None:
                FloatValue = IntValue / Divider
                return "%2.1f %s" % (FloatValue, LabelStr)
            return "%d %s" % (IntValue, LabelStr)
        except Exception as e1:
            self.LogErrorLine("Error in GetParameterBit: " + str(e1))
            return ""

    #-------------GeneratorController:GetParameter------------------------------
    # Hex assumes no Divider and Label - return Hex string
    # ReturnInt assumes no Divier and Label - Return int
    def GetParameter(self, Register, Label = None, Divider = None, Hex = False, ReturnInt = False, ReturnFloat = False):

        try:
            if ReturnInt:
                DefaultReturn = 0
            elif ReturnFloat:
                DefaultReturn = 0.0
            else:
                DefaultReturn = ""

            Value = self.GetRegisterValueFromList(Register)
            if not len(Value):
                return DefaultReturn

            if ReturnInt:
                return int(Value,16)

            if Divider == None and Label == None:
                if Hex:
                    return Value
                elif ReturnFloat:
                    return float(int(Value,16))
                else:
                    return str(int(Value,16))

            IntValue = int(Value,16)
            if not Divider == None:
                FloatValue = IntValue / Divider
                if ReturnFloat:
                    return FloatValue
                if not Label == None:
                    return "%.2f %s" % (FloatValue, Label)
                else:
                    return "%.2f" % (FloatValue)
            elif not Label == None:
                return "%d %s" % (IntValue, Label)
            else:
                return str(int(Value,16))

        except Exception as e1:
            self.LogErrorLine("Error in GetParameter:" + str(e1))
            return ""

    #---------------------GeneratorController::GetConfig------------------------
    # read conf file, used internally, not called by genmon
    # return True on success, else False
    def GetConfig(self):
        True

    #---------------------GeneratorController::SystemInAlarm--------------------
    # return True if generator is in alarm, else False
    def SystemInAlarm(self):
        return False

    #------------ GeneratorController::GetStartInfo ----------------------------
    # return a dictionary with startup info for the gui
    def GetStartInfo(self, NoTile = False):

        StartInfo = {}
        try:
            StartInfo["fueltype"] = self.FuelType
            StartInfo["model"] = self.Model
            StartInfo["nominalKW"] = self.NominalKW
            StartInfo["nominalRPM"] = self.NominalRPM
            StartInfo["nominalfrequency"] = self.NominalFreq
            StartInfo["Controller"] = "Generic Controller Name"
            StartInfo["PowerGraph"] = self.PowerMeterIsSupported()
            StartInfo["NominalBatteryVolts"] = "12"
            StartInfo["UtilityVoltageDisplayed"] = True
            StartInfo["RemoteCommands"] = True
            StartInfo["RemoteButtons"] = False

            if not NoTile:
                StartInfo["tiles"] = []
                for Tile in self.TileList:
                    StartInfo["tiles"].append(Tile.GetStartInfo())

        except Exception as e1:
            self.LogErrorLine("Error in GetStartInfo: " + str(e1))
        return StartInfo

    #------------ GeneratorController::GetStatusForGUI -------------------------
    # return dict for GUI
    def GetStatusForGUI(self):

        Status = {}
        try:
            Status["basestatus"] = self.GetBaseStatus()
            Status["switchstate"] = self.GetSwitchState()
            Status["enginestate"] = self.GetEngineState()
            Status["kwOutput"] = self.GetPowerOutput()
            Status["OutputVoltage"] = "0V"
            Status["BatteryVoltage"] = "0V"
            Status["UtilityVoltage"] = "0V"
            Status["Frequency"] = "0"
            Status["RPM"] = "0"

            # Exercise Info is a dict containing the following:
            ExerciseInfo = collections.OrderedDict()
            ExerciseInfo["Enabled"] = False
            ExerciseInfo["Frequency"] = "Weekly"    # Biweekly, Weekly or Monthly
            ExerciseInfo["Hour"] = "14"
            ExerciseInfo["Minute"] = "00"
            ExerciseInfo["QuietMode"] = "On"
            ExerciseInfo["EnhancedExerciseMode"] = False
            ExerciseInfo["Day"] = "Monday"
            Status["ExerciseInfo"] = ExerciseInfo
        except Exception as e1:
            self.LogErrorLine("Error in GetStatusForGUI: " + str(e1))
        return Status

    #---------------------GeneratorController::DisplayLogs----------------------
    def DisplayLogs(self, AllLogs = False, DictOut = False, RawOutput = False):
        try:
            pass
        except Exception as e1:
            self.LogErrorLine("Error in DisplayLogs: " + str(e1))

    #------------ GeneratorController::DisplayMaintenance ----------------------
    def DisplayMaintenance (self, DictOut = False):
        try:
            pass
        except Exception as e1:
            self.LogErrorLine("Error in DisplayMaintenance: " + str(e1))

    #------------ GeneratorController::DisplayStatus ---------------------------
    def DisplayStatus(self, DictOut = False):
        try:
            pass
        except Exception as e1:
            self.LogErrorLine("Error in DisplayStatus: " + str(e1))

    #------------------- GeneratorController::DisplayOutage --------------------
    def DisplayOutage(self, DictOut = False):
        try:
            pass
        except Exception as e1:
            self.LogErrorLine("Error in DisplayOutage: " + str(e1))

    #------------ GeneratorController::DisplayRegisters ------------------------
    def DisplayRegisters(self, AllRegs = False, DictOut = False):
        try:
            pass
        except Exception as e1:
            self.LogErrorLine("Error in DisplayRegisters: " + str(e1))

    #----------  GeneratorController::SetGeneratorTimeDate----------------------
    # set generator time to system time
    def SetGeneratorTimeDate(self):

        try:
            pass
        except Exception as e1:
            self.LogErrorLine("Error in SetGeneratorTimeDate: " + str(e1))

        return "Not Supported"

    #----------  GeneratorController::SetGeneratorQuietMode---------------------
    # Format of CmdString is "setquiet=yes" or "setquiet=no"
    # return  "Set Quiet Mode Command sent" or some meaningful error string
    def SetGeneratorQuietMode(self, CmdString):
        try:
            pass
        except Exception as e1:
            self.LogErrorLine("Error in SetGeneratorQuietMode: " + str(e1))

        return "Not Supported"

    #----------  GeneratorController::SetGeneratorExerciseTime------------------
    # CmdString is in the format:
    #   setexercise=Monday,13:30,Weekly
    #   setexercise=Monday,13:30,BiWeekly
    #   setexercise=15,13:30,Monthly
    # return  "Set Exercise Time Command sent" or some meaningful error string
    def SetGeneratorExerciseTime(self, CmdString):
        try:
            pass
        except Exception as e1:
            self.LogErrorLine("Error in SetGeneratorExerciseTime: " + str(e1))

        return "Not Supported"

    #----------  GeneratorController::SetGeneratorRemoteStartStop---------------
    # CmdString will be in the format: "setremote=start"
    # valid commands are start, stop, starttransfer, startexercise
    # return string "Remote command sent successfully" or some descriptive error
    # string if failure
    def SetGeneratorRemoteStartStop(self, CmdString):
        try:
            pass
        except Exception as e1:
            self.LogErrorLine("Error in SetGeneratorRemoteStartStop: " + str(e1))

        return "Not Supported"

    #----------  GeneratorController:GetController  ----------------------------
    # return the name of the controller, if Actual == False then return the
    # controller name that the software has been instructed to use if overridden
    # in the conf file
    def GetController(self, Actual = True):
        return "Test Controller"

    #----------  GeneratorController:ComminicationsIsActive  -------------------
    # Called every 2 seconds, if communictions are failing, return False, otherwise
    # True
    def ComminicationsIsActive(self):
        return False

    #----------  GeneratorController:ResetCommStats  ---------------------------
    # reset communication stats, normally just a call to
    #   self.ModBus.ResetCommStats() if modbus is used
    def ResetCommStats(self):
        self.ModBus.ResetCommStats()

    #----------  GeneratorController:RemoteButtonsSupported  --------------------
    # return true if Panel buttons are settable via the software
    def RemoteButtonsSupported(self):
        return False
    #----------  GeneratorController:PowerMeterIsSupported  --------------------
    # return true if GetPowerOutput is supported
    def PowerMeterIsSupported(self):
        return False

    #---------------------GeneratorController::GetPowerOutput-------------------
    # returns current kW
    # rerturn empty string ("") if not supported,
    # return kW with units i.e. "2.45kW"
    def GetPowerOutput(self, ReturnFloat = False):
        return ""

    #----------  GeneratorController:GetCommStatus  ----------------------------
    # return Dict with communication stats
    def GetCommStatus(self):
        return self.ModBus.GetCommStats()

    #------------ GeneratorController:GetBaseStatus ----------------------------
    # return one of the following: "ALARM", "SERVICEDUE", "EXERCISING", "RUNNING",
    # "RUNNING-MANUAL", "OFF", "MANUAL", "READY"
    def GetBaseStatus(self):
        return "OFF"

    #------------ GeneratorController:GetOneLineStatus -------------------------
    # returns a one line status for example : switch state and engine state
    def GetOneLineStatus(self):
        return "Unknown"
    #------------ GeneratorController:RegRegValue ------------------------------
    def GetRegValue(self, CmdString):

        # extract quiet mode setting from Command String
        # format is setquiet=yes or setquiet=no
        msgbody = "Invalid command syntax for command getregvalue"
        try:
            #Format we are looking for is "getregvalue=01f4"
            CmdList = CmdString.split("=")
            if len(CmdList) != 2:
                self.LogError("Validation Error: Error parsing command string in GetRegValue (parse): " + CmdString)
                return msgbody

            CmdList[0] = CmdList[0].strip()

            if not CmdList[0].lower() == "getregvalue":
                self.LogError("Validation Error: Error parsing command string in GetRegValue (parse2): " + CmdString)
                return msgbody

            Register = CmdList[1].strip()

            RegValue = self.GetRegisterValueFromList(Register)

            if RegValue == "":
                self.LogError("Validation Error: Register  not known:" + Register)
                msgbody = "Unsupported Register: " + Register
                return msgbody

            msgbody = RegValue

        except Exception as e1:
            self.LogErrorLine("Validation Error: Error parsing command string in GetRegValue: " + CmdString)
            self.LogError( str(e1))
            return msgbody

        return msgbody


    #------------ GeneratorController:ReadRegValue -----------------------------
    def ReadRegValue(self, CmdString):

        # extract quiet mode setting from Command String
        #Format we are looking for is "readregvalue=01f4"
        msgbody = "Invalid command syntax for command readregvalue"
        try:

            CmdList = CmdString.split("=")
            if len(CmdList) != 2:
                self.LogError("Validation Error: Error parsing command string in ReadRegValue (parse): " + CmdString)
                return msgbody

            CmdList[0] = CmdList[0].strip()

            if not CmdList[0].lower() == "readregvalue":
                self.LogError("Validation Error: Error parsing command string in ReadRegValue (parse2): " + CmdString)
                return msgbody

            Register = CmdList[1].strip()

            RegValue = self.ModBus.ProcessMasterSlaveTransaction( Register, 1, ReturnValue = True)

            if RegValue == "":
                self.LogError("Validation Error: Register  not known (ReadRegValue):" + Register)
                msgbody = "Unsupported Register: " + Register
                return msgbody

            msgbody = RegValue

        except Exception as e1:
            self.LogErrorLine("Validation Error: Error parsing command string in ReadRegValue: " + CmdString)
            self.LogError( str(e1))
            return msgbody

        return msgbody
    #------------ GeneratorController:DisplayOutageHistory----------------------
    def DisplayOutageHistory(self):

        LogHistory = []

        if not len(self.OutageLog):
            return ""
        try:
            # check to see if a log file exist yet
            if not os.path.isfile(self.OutageLog):
                return ""

            OutageLog = []

            with open(self.OutageLog,"r") as OutageFile:     #opens file

                for line in OutageFile:
                    line = line.strip()                   # remove whitespace at beginning and end

                    if not len(line):
                        continue
                    if line[0] == "#":              # comment?
                        continue
                    Items = line.split(",")
                    if len(Items) != 2 and len(Items) != 3:
                        continue
                    if len(Items) == 3:
                        strDuration = Items[1] + "," + Items[2]
                    else:
                        strDuration = Items[1]

                    OutageLog.insert(0, [Items[0], strDuration])
                    if len(OutageLog) > 50:     # limit log to 50 entries
                        OutageLog.pop()

            for Items in OutageLog:
                LogHistory.append("%s, Duration: %s" % (Items[0], Items[1]))

            return LogHistory

        except Exception as e1:
            self.LogErrorLine("Error in  DisplayOutageHistory: " + str(e1))
            return []
    #------------ GeneratorController::PrunePowerLog----------------------------
    def PrunePowerLog(self, Minutes):

        if not Minutes:
            return self.ClearPowerLog()

        try:
            CmdString = "power_log_json=%d" % Minutes
            PowerLog = self.GetPowerHistory(CmdString, NoReduce = True)

            LogSize = os.path.getsize(self.PowerLog)

            if float(LogSize) / (1024*1024) >= self.PowerLogMaxSize * 0.98:
                msgbody = "The kwlog file size is 98% of the maximum. Once the log reaches 100% of the maximum size the log will be reset."
                self.MessagePipe.SendMessage("Notice: Log file size warning" , msgbody, msgtype = "warn")

            # is the file size too big?
            if float(LogSize) / (1024*1024) >= self.PowerLogMaxSize:
                self.ClearPowerLog()
                self.LogError("Power Log entries deleted due to size reaching maximum.")
                return "OK"

            self.ClearPowerLog(NoCreate = True)
            # Write oldest log entries first
            for Items in reversed(PowerLog):
                self.LogToFile(self.PowerLog, Items[0], Items[1])

            if not os.path.isfile(self.PowerLog):
                TimeStamp = datetime.datetime.now().strftime('%x %X')
                self.LogToFile(self.PowerLog, TimeStamp, "0.0")

            LogSize = os.path.getsize(self.PowerLog)
            if LogSize == 0:
                TimeStamp = datetime.datetime.now().strftime('%x %X')
                self.LogToFile(self.PowerLog, TimeStamp, "0.0")

            return "OK"

        except Exception as e1:
            self.LogErrorLine("Error in  PrunePowerLog: " + str(e1))
            return "Error in  PrunePowerLog: " + str(e1)

    #------------ GeneratorController::ClearPowerLog----------------------------
    def ClearPowerLog(self, NoCreate = False):

        try:
            if not len(self.PowerLog):
                return "Power Log Disabled"

            if not os.path.isfile(self.PowerLog):
                return "Power Log is empty"
            try:
                os.remove(self.PowerLog)
            except:
                pass

            if not NoCreate:
                # add zero entry to note the start of the log
                TimeStamp = datetime.datetime.now().strftime('%x %X')
                self.LogToFile(self.PowerLog, TimeStamp, "0.0")

            return "Power Log cleared"
        except Exception as e1:
            self.LogErrorLine("Error in  ClearPowerLog: " + str(e1))
            return "Error in  ClearPowerLog: " + str(e1)

    #------------ GeneratorController::ReducePowerSamples-----------------------
    def ReducePowerSamplesOld(self, PowerList, MaxSize):

        if MaxSize == 0:
            self.LogError("RecducePowerSamples: Error: Max size is zero")
            return []

        if len(PowerList) < MaxSize:
            self.LogError("RecducePowerSamples: Error: Can't reduce ")
            return PowerList

        try:
            Sample = int(len(PowerList) / MaxSize)
            Remain = int(len(PowerList) % MaxSize)

            NewList = []
            Count = 0
            for Count in range(len(PowerList)):
                TimeStamp, KWValue = PowerList[Count]
                if float(KWValue) == 0:
                        NewList.append([TimeStamp,KWValue])
                elif ( Count % Sample == 0 ):
                    NewList.append([TimeStamp,KWValue])

            # if we have too many entries due to a remainder or not removing zero samples, then delete some
            if len(NewList) > MaxSize:
                return RemovePowerSamples(NewList, MaxSize)
        except Exception as e1:
            self.LogErrorLine("Error in RecducePowerSamples: %s" % str(e1))
            return PowerList

        return NewList

    #------------ GeneratorController::RemovePowerSamples-----------------------
    def RemovePowerSamples(List, MaxSize):

        try:
            if len(List) <= MaxSize:
                self.LogError("RemovePowerSamples: Error: Can't remove ")
                return List

            Extra = len(List) - MaxSize
            for Count in range(Extra):
                    # assume first and last sampels are zero samples so don't select thoes
                    self.MarkNonZeroKwEntry(List, random.randint(1, len(List) - 2))

            TempList = []
            for TimeStamp, KWValue in List:
                if not TimeStamp == "X":
                    TempList.append([TimeStamp, KWValue])
            return TempList
        except Exception as e1:
            self.LogErrorLine("Error in RemovePowerSamples: %s" % str(e1))
            return List

    #------------ GeneratorController::MarkNonZeroKwEntry-----------------------
    #       RECURSIVE
    def MarkNonZeroKwEntry(self, List, Index):

        try:
            TimeStamp, KwValue = List[Index]
            if not KwValue == "X" and not float(KwValue) == 0.0:
                List[Index] = ["X", "X"]
                return
            else:
                MarkNonZeroKwEntry(List, Index - 1)
                return
        except Exception as e1:
            self.LogErrorLine("Error in MarkNonZeroKwEntry: %s" % str(e1))
        return

    #------------ GeneratorController::ReducePowerSamples-----------------------
    def ReducePowerSamples(self, PowerList, MaxSize):

        if MaxSize == 0:
            self.LogError("RecducePowerSamples: Error: Max size is zero")
            return []

        periodMaxSamples = MaxSize
        NewList = []
        try:
            CurrentTime = datetime.datetime.now()
            secondPerSample = 0
            prevMax = 0
            currMax = 0
            currTime = CurrentTime
            prevTime = CurrentTime + datetime.timedelta(minutes=1)
            currSampleTime = CurrentTime
            prevBucketTime = CurrentTime # prevent a 0 to be written the first time
            nextBucketTime = CurrentTime - datetime.timedelta(seconds=1)

            for Count in range(len(PowerList)):
               TimeStamp, KWValue = PowerList[Count]
               struct_time = time.strptime(TimeStamp, "%x %X")
               delta_sec = (CurrentTime - datetime.datetime.fromtimestamp(time.mktime(struct_time))).total_seconds()
               if 0 <= delta_sec <= datetime.timedelta(minutes=60).total_seconds():
                   secondPerSample = int(datetime.timedelta(minutes=60).total_seconds() / periodMaxSamples)
               if datetime.timedelta(minutes=60).total_seconds() <= delta_sec <=  datetime.timedelta(hours=24).total_seconds():
                   secondPerSample = int(datetime.timedelta(hours=23).total_seconds() / periodMaxSamples)
               if datetime.timedelta(hours=24).total_seconds() <= delta_sec <= datetime.timedelta(days=7).total_seconds():
                   secondPerSample = int(datetime.timedelta(days=6).total_seconds() / periodMaxSamples)
               if datetime.timedelta(days=7).total_seconds() <= delta_sec <= datetime.timedelta(days=31).total_seconds():
                   secondPerSample = int(datetime.timedelta(days=25).total_seconds() / periodMaxSamples)

               currSampleTime = CurrentTime - datetime.timedelta(seconds=(int(delta_sec / secondPerSample)*secondPerSample))
               if (currSampleTime != currTime):
                   if ((currMax > 0) and (prevBucketTime != prevTime)):
                       NewList.append([prevBucketTime.strftime('%x %X'), 0.0])
                   if ((currMax > 0) or ((currMax == 0) and (prevMax > 0))):
                       NewList.append([currTime.strftime('%x %X'), currMax])
                   if ((currMax > 0) and (nextBucketTime != currSampleTime)):
                       NewList.append([nextBucketTime.strftime('%x %X'), 0.0])
                   prevMax = currMax
                   prevTime = currTime
                   currMax = KWValue
                   currTime = currSampleTime
                   prevBucketTime  = CurrentTime - datetime.timedelta(seconds=((int(delta_sec / secondPerSample)+1)*secondPerSample))
                   nextBucketTime  = CurrentTime - datetime.timedelta(seconds=((int(delta_sec / secondPerSample)-1)*secondPerSample))
               else:
                   currMax = max(currMax, KWValue)


            NewList.append([currTime.strftime('%x %X'), currMax])
        except Exception as e1:
            self.LogErrorLine("Error in RecducePowerSamples: %s" % str(e1))
            return PowerList

        return NewList

    #------------ GeneratorController::GetPowerHistory--------------------------
    def GetPowerHistory(self, CmdString, NoReduce = False):

        KWHours = False
        FuelConsumption = False
        msgbody = "Invalid command syntax for command power_log_json"

        try:
            if not len(self.PowerLog):
                # power log disabled
                return []

            if not len(CmdString):
                self.LogError("Error in GetPowerHistory: Invalid input")
                return []

            #Format we are looking for is "power_log_json=5" or "power_log_json" or "power_log_json=1000,kw"
            CmdList = CmdString.split("=")

            if len(CmdList) > 2:
                self.LogError("Validation Error: Error parsing command string in GetPowerHistory (parse): " + CmdString)
                return msgbody

            CmdList[0] = CmdList[0].strip()

            if not CmdList[0].lower() == "power_log_json":
                self.LogError("Validation Error: Error parsing command string in GetPowerHistory (parse2): " + CmdString)
                return msgbody

            if len(CmdList) == 2:
                ParseList = CmdList[1].split(",")
                if len(ParseList) == 1:
                    Minutes = int(CmdList[1].strip())
                elif len(ParseList) == 2:
                    Minutes = int(ParseList[0].strip())
                    if ParseList[1].strip().lower() == "kw":
                        KWHours = True
                    elif ParseList[1].strip().lower() == "fuel":
                        FuelConsumption = True
                else:
                    self.LogError("Validation Error: Error parsing command string in GetPowerHistory (parse3): " + CmdString)
                    return msgbody

            else:
                Minutes = 0
        except Exception as e1:
            self.LogErrorLine("Error in  GetPowerHistory (Parse): %s : %s" % (CmdString,str(e1)))
            return msgbody

        try:
            # check to see if a log file exist yet
            if not os.path.isfile(self.PowerLog):
                return []

            PowerList = []

            with open(self.PowerLog,"r") as LogFile:     #opens file
                CurrentTime = datetime.datetime.now()
                try:
                    for line in LogFile:
                        line = line.strip()                  # remove whitespace at beginning and end

                        if not len(line):
                            continue
                        if line[0] == "#":                  # comment
                            continue
                        Items = line.split(",")
                        if len(Items) != 2:
                            continue
                        # remove any kW labels that may be there
                        Items[1] = self.removeAlpha(Items[1])

                        if Minutes:
                            struct_time = time.strptime(Items[0], "%x %X")
                            LogEntryTime = datetime.datetime.fromtimestamp(time.mktime(struct_time))
                            Delta = CurrentTime - LogEntryTime
                            if self.GetDeltaTimeMinutes(Delta) < Minutes :
                                PowerList.insert(0, [Items[0], Items[1]])
                        else:
                            PowerList.insert(0, [Items[0], Items[1]])
                    #Shorten list to 1000 if specific duration requested
                    if not KWHours and len(PowerList) > 500 and Minutes and not NoReduce:
                        PowerList = self.ReducePowerSamples(PowerList, 500)
                except Exception as e1:
                    self.LogErrorLine("Error in  GetPowerHistory (parse file): " + str(e1))
                    # continue to the next line

            if KWHours:
                AvgPower, TotalSeconds = self.GetAveragePower(PowerList)
                return "%.2f" % ((TotalSeconds / 3600) * AvgPower)
            if FuelConsumption:
                AvgPower, TotalSeconds = self.GetAveragePower(PowerList)
                Consumption, Label = self.GetFuelConsumption(AvgPower, TotalSeconds)
                if Consumption == None:
                    return "Unknown"
                return "%.2f %s" % (Consumption, Label)

            return PowerList

        except Exception as e1:
            self.LogErrorLine("Error in  GetPowerHistory: " + str(e1))
            msgbody = "Error in  GetPowerHistory: " + str(e1)
            return msgbody

    #----------  GeneratorController::GetAveragePower---------------------------
    # a list of the power log is passed in (already parsed for a time period)
    # returns a time period and average power used for that time period
    def GetAveragePower(self, PowerList):

        try:
            TotalTime = datetime.timedelta(seconds=0)
            Entries = 0
            TotalPower = 0.0
            LastPower = 0.0
            LastTime = None
            for Items in PowerList:
                Power = float(Items[1])
                struct_time = time.strptime(Items[0], "%x %X")
                LogEntryTime = datetime.datetime.fromtimestamp(time.mktime(struct_time))

                if LastTime == None or Power == 0:
                    TotalTime += LogEntryTime - LogEntryTime
                else:
                    TotalTime += LastTime - LogEntryTime
                    TotalPower += (Power + LastPower) / 2
                    Entries += 1
                LastTime = LogEntryTime
                LastPower = Power

            if Entries == 0:
                return 0,0
            TotalPower = TotalPower / Entries
            return TotalPower, TotalTime.total_seconds()
        except Exception as e1:
            self.LogErrorLine("Error in  GetAveragePower: " + str(e1))
            return 0, 0

    #----------  GeneratorController::PowerMeter--------------------------------
    #----------  Monitors Power Output
    def PowerMeter(self):

        # make sure system is up and running otherwise we will not know which controller is present
        time.sleep(1)
        while True:

            if self.InitComplete:
                break
            if self.WaitForExit("PowerMeter", 1):
                return

        # if power meter is not supported do nothing.
        # Note: This is done since if we killed the thread here
        while not self.PowerMeterIsSupported() or not len(self.PowerLog):
            if self.WaitForExit("PowerMeter", 60):
                return

        # if log file is empty or does not exist, make a zero entry in log to denote start of collection
        if not os.path.isfile(self.PowerLog) or os.path.getsize(self.PowerLog) == 0:
            TimeStamp = datetime.datetime.now().strftime('%x %X')
            self.LogError("Creating Power Log: " + self.PowerLog)
            self.LogToFile(self.PowerLog, TimeStamp, "0.0")

        LastValue = 0.0
        LastPruneTime = datetime.datetime.now()
        while True:
            try:
                if self.WaitForExit("PowerMeter", 10):
                    return

                # Housekeeping on kw Log
                if self.GetDeltaTimeMinutes(datetime.datetime.now() - LastPruneTime) > 1440 :     # check every day
                    self.PrunePowerLog(43800 * 36)   # delete log entries greater than three years
                    LastPruneTime = datetime.datetime.now()

                # Time to exit?
                if self.IsStopSignaled("PowerMeter"):
                    return
                KWFloat = self.GetPowerOutput(ReturnFloat = True)

                if LastValue == KWFloat:
                    continue

                if LastValue == 0:
                    StartTime = datetime.datetime.now() - datetime.timedelta(seconds=1)
                    TimeStamp = StartTime.strftime('%x %X')
                    self.LogToFile(self.PowerLog, TimeStamp, str(LastValue))

                LastValue = KWFloat
                # Log to file
                TimeStamp = datetime.datetime.now().strftime('%x %X')
                self.LogToFile(self.PowerLog, TimeStamp, str(KWFloat))

            except Exception as e1:
                self.LogErrorLine("Error in PowerMeter: " + str(e1))

    #----------  GeneratorController::GetEstimatedFuelInTank--------------------
    def GetEstimatedFuelInTank(self, ReturnFloat = False):

        if ReturnFloat:
            DefaultReturn = 0.0
        else:
            DefaultReturn = "0"

        if self.TankSize == None or self.TankSize == "0" or self.TankSize == "":
            return DefaultReturn
        try:
            FuelUsed = self.GetPowerHistory("power_log_json=0,fuel", NoReduce = True)
            if FuelUsed == "Unknown" or not len(FuelUsed):
                return DefaultReturn
            FuelUsed = self.removeAlpha(FuelUsed)
            FuelLeft = float(self.TankSize) - float(FuelUsed)
            if FuelLeft < 0:
                FuelLeft = 0.0

            if self.UseMetric:
                Units = "L"
            else:
                Units = "gal"
            if ReturnFloat:
                return FuelLeft
            return "%.2f %s" % (FuelLeft, Units)
        except Exception as e1:
            self.LogErrorLine("Error in GetEstimatedFuelInTank: " + str(e1))
            return DefaultReturn

    #----------  GeneratorController::FuelGuageSupported------------------------
    def FuelGuageSupported(self):
        return False
    #----------  GeneratorController::FuelConsumptionSupported------------------
    def FuelConsumptionSupported(self):
        return False

    #----------  GeneratorController::GetFuelConsumption------------------------
    def GetFuelConsumption(self, kw, seconds):
        try:
            Polynomial = self.GetFuelConsumptionPolynomial()
            if Polynomial == None or len(Polynomial) != 4:
                return None, ""

            Load = kw / int(self.NominalKW)
            # Consumption of load for 1 hour
            Consumption = (Polynomial[0] * (Load ** 2)) + (Polynomial[1] * Load) + Polynomial[2]

            # now compensate for time
            Consumption = (seconds / 3600) * Consumption

            if self.UseMetric:
                Consumption = Consumption * 3.78541
                return round(Consumption, 4), "L"     # convert to Liters
            else:
                return round(Consumption, 4), Polynomial[3]
        except Exception as e1:
            self.LogErrorLine("Error in GetFuelConsumption: " + str(e1))
            return None, ""
    #----------  GeneratorController::GetFuelConsumptionPolynomial--------------
    def GetFuelConsumptionPolynomial(self):
        return None
    #----------  GeneratorController::Close-------------------------------------
    def Close(self):

        try:
            # Controller
            self.IsStopping = True
            try:
                self.InitCompleteEvent.set()
            except:
                pass

            if self.ModBus != None:
                try:
                    self.ModBus.Close()
                except:
                    pass
            try:
                if self.EnableDebug:
                    self.KillThread("DebugThread")
            except:
                pass

            try:
                self.KillThread("ProcessThread")
            except:
                pass

            try:
                self.KillThread("CheckAlarmThread")
            except:
                pass

            try:
                self.KillThread("PowerMeter")
            except:
                pass

        except Exception as e1:
            self.LogErrorLine("Error Closing Controller: " + str(e1))

        with self.CriticalLock:
            self.InitComplete = False
