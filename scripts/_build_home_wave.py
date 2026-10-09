"""Generate HomeView.xaml with the static equalizer waveform.

Run from repo root:  python scripts/_build_home_wave.py
"""
import math
import pathlib

N = 60
W = 8
GAP = 4
AMP = 36
CY = 50

PHASES = (0.0, math.pi / 2.7, math.pi / 5)


def height(i: int) -> float:
    t = i / N
    v = (
        math.sin(2 * math.pi * 1.0 * t + PHASES[0])
        + math.sin(2 * math.pi * 2.0 * t + PHASES[1]) * 0.6
        + math.sin(2 * math.pi * 3.0 * t + PHASES[2]) * 0.3
    ) / 1.9
    return max(2.0, AMP * abs(v))


lines = []
for i in range(N):
    h = height(i)
    x = i * (W + GAP)
    y = CY - h / 2
    op = 0.55 + 0.45 * abs(math.sin(i / 4))
    lines.append(
        f'<Rectangle Width="{W}" Height="{h:.1f}" '
        f'Canvas.Left="{x:.1f}" Canvas.Top="{y:.1f}" '
        f'RadiusX="2" RadiusY="2" Fill="{{StaticResource G.Wave}}" '
        f'Opacity="{op:.2f}" IsHitTestVisible="False"/>'
    )
bars = "\n      ".join(lines)

xml = f'''<UserControl x:Class="Genie.Desktop.Views.HomeView"
             xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
             xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
             xmlns:vm="clr-namespace:Genie.Desktop.ViewModels"
             d:DataContext="{{d:DesignInstance Type=vm:HomeViewModel}}"
             xmlns:d="http://schemas.microsoft.com/expression/blend/2008"
             xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
             mc:Ignorable="d">

    <Grid Margin="0">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <!-- Hero: branded strip + waveform equalizer + result panel.
             Static bars, no animation - costs nothing and stays calm. -->
        <Border Grid.Row="0" Margin="24,24,24,0"
                Background="{{StaticResource G.HeroBg}}"
                BorderBrush="{{StaticResource G.LineSoft}}"
                BorderThickness="1" CornerRadius="8" ClipToBounds="True">
            <Grid>
                <Grid.RowDefinitions>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="*"/>
                </Grid.RowDefinitions>

                <StackPanel Grid.Row="0" Margin="22,20,22,0">
                    <StackPanel Orientation="Horizontal">
                        <Image Source="/assets/Genie_brand_256.png"
                               Width="40" Height="40" Stretch="UniformToFill"
                               Margin="0,0,12,0" ClipToBounds="True">
                            <Image.Clip>
                                <EllipseGeometry Center="20,20" RadiusX="20" RadiusY="20"/>
                            </Image.Clip>
                        </Image>
                        <StackPanel VerticalAlignment="Center">
                            <TextBlock Text="GENIE" FontWeight="SemiBold" FontSize="16"
                                       Foreground="{{StaticResource G.Text}}"/>
                            <TextBlock Text="{{Binding Greeting}}" FontSize="13"
                                       Foreground="{{StaticResource G.TextMid}}"/>
                        </StackPanel>
                    </StackPanel>
                </StackPanel>

                <Grid Grid.Row="1" Margin="22,16,22,22">
                    <Grid.RowDefinitions>
                        <RowDefinition Height="*"/>
                        <RowDefinition Height="Auto"/>
                    </Grid.RowDefinitions>

                    <Canvas Grid.Row="0" Height="100" IsHitTestVisible="False"
                            ClipToBounds="True" SnapsToDevicePixels="True"
                            RenderOptions.BitmapScalingMode="LowQuality">
      {bars}
                    </Canvas>

                    <Border Grid.Row="1" Margin="0,14,0,0" Padding="14"
                            Background="#CC060912"
                            BorderBrush="{{StaticResource G.LineSoft}}"
                            BorderThickness="1" CornerRadius="6">
                        <Grid>
                            <ScrollViewer VerticalScrollBarVisibility="Auto" MaxHeight="180">
                                <TextBlock Text="{{Binding LastResult}}" TextWrapping="Wrap"
                                           Style="{{StaticResource G.BodyText}}"/>
                            </ScrollViewer>
                            <TextBlock HorizontalAlignment="Center" VerticalAlignment="Center"
                                       TextAlignment="Center" TextWrapping="Wrap"
                                       Style="{{StaticResource G.Dim}}"
                                       Visibility="{{Binding HasResult, Converter={{StaticResource InverseBoolToVisibility}}}}"
                                       Text="Ask GENIE anything. It will plan, act, and report what it did.&#x0a;For multi-step work, open Missions."/>
                        </Grid>
                    </Border>
                </Grid>
            </Grid>
        </Border>

        <!-- Command row. Enter sends, Shift+Enter keeps the newline. -->
        <Grid Grid.Row="2" Margin="24,16,24,24">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <TextBox Grid.Column="0" Style="{{StaticResource G.TextBox}}"
                     Text="{{Binding CommandText, UpdateSourceTrigger=PropertyChanged}}"
                     IsEnabled="{{Binding NotBusy}}">
                <TextBox.InputBindings>
                    <KeyBinding Key="Enter" Command="{{Binding SendCommand}}"/>
                </TextBox.InputBindings>
            </TextBox>
            <Button Grid.Column="1" Margin="10,0,0,0" Style="{{StaticResource G.ButtonPrimary}}"
                    Padding="22,9" Content="Send" Command="{{Binding SendCommand}}"/>
        </Grid>
    </Grid>
</UserControl>
'''

pathlib.Path("ui/windows/Genie.Desktop/Views/HomeView.xaml").write_text(xml, encoding="utf-8", newline="")
print("HomeView.xaml written", len(xml), "bytes")