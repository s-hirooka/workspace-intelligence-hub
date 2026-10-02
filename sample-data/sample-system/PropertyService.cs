namespace Sample.System;
public class PropertyService
{
    public async Task SendToWordPressAsync(Property property)
    {
        // Public sample only. No real endpoint or credentials.
        await wordpressClient.PostAsync("/sample-properties", property);
    }
}
